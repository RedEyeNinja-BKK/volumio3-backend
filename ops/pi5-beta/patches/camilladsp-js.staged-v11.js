"use strict";

const { execSync } = require("child_process");
const { spawn } = require("child_process");
const fs = require("fs");

let counter = 0;

/**
 * CamillaDsp class to handle the external process
 * spawned as child process
 */
let CamillaDsp = function (logger) {

    const cdPath = "/data/plugins/audio_interface/fusiondsp/camilladsp"
    const cdLog = "/tmp/camilladsp.log";
    const cdLogLevel = "warn";
    const cdPortWs = 9876;
    const cdPathConfig = "/data/configuration/audio_interface/fusiondsp/camilladsp.yml";
    const cdFifo = "/tmp/fusiondspfifo";

    // Respawn backoff settings
    const baseRespawnDelayMs = 1000;
    const maxRespawnDelayMs = 10000;
    const maxConsecutiveRespawns = 10;
    const respawnCountResetMs = 30000; // Reset count if up for this long

    // v8 (pi5-beta 2026-09-21): a closed FIFO is a normal stop, not a failure.
    const cleanExitRespawnDelayMs = 100; // respawn at once, as the listener comment intends
    const cleanExitTrustMs = 2000;       // up this long => a real stop, not a spawn loop

    let run = false;
    let camilla = null;
    let uniqueid = ++counter;

    // Respawn backoff state
    let consecutiveRespawns = 0;
    let lastSpawnTime = 0;
    let respawnStopped = false;

    // v11: the fifo keeper. See the header of this patch for the measurement and the reasoning.
    let fifoKeeperFd = null;
    let fifoKeeperTimer = null;

    let closeFifoKeeper = function() {
        if (fifoKeeperTimer) {
            clearTimeout(fifoKeeperTimer);
            fifoKeeperTimer = null;
        }
        if (fifoKeeperFd !== null) {
            try {
                fs.closeSync(fifoKeeperFd);
            } catch (e) {
                // already gone
            }
            fifoKeeperFd = null;
            logger.debug('camilladsp fifo keeper released the fifo');
        }
    };

    let openFifoKeeper = function(deadline) {
        if (fifoKeeperFd !== null || run === false)
            return;

        // O_NONBLOCK is load-bearing: without it this open blocks until camilladsp has opened
        // the read end, freezing the plugin's event loop.
        const flags = fs.constants.O_WRONLY | fs.constants.O_NONBLOCK;

        fs.open(cdFifo, flags, function(err, fd) {
            if (run === false) {
                if (fd !== undefined && fd !== null) {
                    try { fs.closeSync(fd); } catch (e) { /* nothing to close */ }
                }
                return;
            }
            if (!err) {
                fifoKeeperFd = fd;
                logger.info(`camilladsp fifo keeper holding the fifo open (fd ${fd})`);
                return;
            }
            // ENXIO means no reader has the fifo open yet - expected until camilladsp starts.
            if (err.code === 'ENXIO' && Date.now() < deadline) {
                fifoKeeperTimer = setTimeout(function() { openFifoKeeper(deadline); }, 50);
                return;
            }
            logger.warn(`camilladsp fifo keeper could not open the fifo: ${err}`);
        });
    };

    /**
     * Listener for event sent on camilladsp process termination.
     * The process may terminate either because FIFO has been closed (hence
     * we need to respawn the process immediately) or because of an error.
     * In case of error, we wait with exponential backoff to avoid hogging CPU.
     * If too many consecutive quick respawns occur, stop respawning entirely.
     */
    let listenerClose = function(code, signal) {

        let timeout = 0;
        let uptime = Date.now() - lastSpawnTime;

        logger.debug("close event");

        // Nullify the camilla process since it has been fully terminated
        camilla = null;

        // .stop() has been called, hence the process is supposed to
        // not to be respawned. Just stop here in case.
        if (run === false)
            return;

        // If respawning was stopped due to too many failures, don't respawn
        if (respawnStopped) {
            logger.warn(`camilladsp respawn stopped due to repeated failures; not respawning`);
            return;
        }

        // Check uptime: if process was up long enough, reset respawn count
        if (uptime >= respawnCountResetMs) {
            consecutiveRespawns = 0;
        }

        // v8 (pi5-beta 2026-09-21): see the listener's comment above - a closed FIFO means
        // respawn immediately. It did not: the clean code-0 exit that follows every ordinary
        // playback stop was counted as a failure, so the delay doubled on each handover
        // (measured: 100, 200, 400, 800, 1600 ms of silence with no fifo reader). A clean
        // exit that came well after spawn is a normal stop, so it must not consume the
        // failure budget. A clean exit almost immediately after spawn is a spawn/exit loop
        // and still counts, so the maxConsecutiveRespawns protection is intact.
        const cleanExitAfterPlayback = (code === 0 && uptime >= cleanExitTrustMs);

        if (cleanExitAfterPlayback) {

            // Reviewed finding 7 (2026-09-21): a trusted clean exit is a COMPLETED session, so
            // clear any failure streak. Without this, a count left over from earlier early
            // exits makes a later early exit look like attempt N rather than attempt 1, and the
            // "this was a normal stop" semantics are only half applied.
            consecutiveRespawns = 0;

        } else {

            // Increment consecutive respawn counter
            consecutiveRespawns++;

            // Check if we've exceeded max consecutive respawns
            if (consecutiveRespawns > maxConsecutiveRespawns) {
                logger.error(`camilladsp exceeded max consecutive respawns (${maxConsecutiveRespawns}); stopping respawn. Plugin restart required.`);
                // v11: respawning is abandoned, so the keeper must go too - otherwise a writer
                // would be left on a fifo with no reader.
                closeFifoKeeper();
                respawnStopped = true;
                return;
            }
        }

        // Calculate backoff delay: baseDelay * 2^(respawnCount-1), capped at maxDelay
        // A clean exit after real playback respawns at a short FIXED delay; anything else
        // keeps the exponential backoff.
        if (cleanExitAfterPlayback) {
            timeout = cleanExitRespawnDelayMs;
        } else {
            timeout = Math.min(baseRespawnDelayMs * Math.pow(2, consecutiveRespawns - 1), maxRespawnDelayMs);
        }

        logger.debug(`camilladsp close event, exit code ${code}, signal ${signal}`);
        logger.info(`camilladsp respawn in ${timeout} ms (attempt ${consecutiveRespawns}/${maxConsecutiveRespawns})`);

        setTimeout(function() {

            if (run === false)
                return;

            // In case of error, cleanup the FIFO before starting, so it won't be
            // kept in wait state and stall the whole pipeline
            if (code > 0) {
                try {
                    execSync("/bin/dd if=/tmp/fusiondspfifo of=/dev/null bs=32k iflag=nonblock");
                } catch (e) {
                    // pass
                }
            }

            processSpawn();

        }, timeout);

    };

    /**
     * Listener for "exit" process: here process is terminated but
     * stdio is not yet closed (and we may still not have an exit code)
     */
    let listenerExit = function(code, signal) {

        logger.debug(`camilladsp exit event, exit code ${code}, signal ${signal}`);

    };

    /**
     * Private function to spawn the camilladsp process.
     * If the process is already started (ie: camilla !== null), does not
     * spawn another process
     */
    let processSpawn = function() {

        let args;

        if (camilla !== null)
            return;

        args = [
            "-p",
            cdPortWs,
            "-o",
            cdLog,
            "-l",
            cdLogLevel,
            cdPathConfig
        ];

        logger.debug(`camilladsp spawning process`);

        camilla = spawn(cdPath, args);
        lastSpawnTime = Date.now();

        // v11: hold the fifo open so the next handover does not tear the DAC stream down.
        openFifoKeeper(Date.now() + 2000);

     //   logger.info(`camilladsp spawned new process with pid ${camilla.pid}, instance ${uniqueid}, run: ${run}`);

        //camilla.on("exit", listenerExit);
        camilla.on("close", listenerClose);

    };

    /**
     * Private function to stop camilladsp process. If there is no process running
     * (ie: camilla === null), does not do anything
     */
    let processStop = function() {

        let pid;

        try {

            // v11: release the keeper first, so a deliberately stopped engine never leaves a
            // writer on a fifo with no reader.
            closeFifoKeeper();

            if (camilla === null)
                return;

            pid = camilla.pid;

            logger.info(`camilladsp stopping service pid ${pid}...`);

            camilla.kill();

            // Hacky way to make this function synchronous
            execSync(`while true; do grep camilladsp /proc/${pid}/cmdline || break; sleep 0.1; done`);

            logger.debug(`camilladsp stopped pid ${pid}`);

        } catch (e) {

            logger.error(`camilladsp processStop exception. Reason: ${e}`);

        }

    };

    /**
     * Public function to spawn the camilladsp process and keep it
     * running in the background
     */
    this.start = function() {

        run = true;

        // Reset respawn state on explicit start
        consecutiveRespawns = 0;
        respawnStopped = false;

        processSpawn();

        logger.info(`camilladsp service started and running in background, instance ${uniqueid}`);

    };

    /**
     * Public function to terminate the camilladsp process and stop
     * it from respawning
     */
    this.stop = function() {

        run = false;

        processStop();

        logger.info(`camilladsp service terminated, instance ${uniqueid}`);

    }

};

module.exports = { CamillaDsp };

