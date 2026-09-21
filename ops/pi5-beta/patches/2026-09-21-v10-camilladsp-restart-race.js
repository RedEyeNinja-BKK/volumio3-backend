"use strict";

const { execSync } = require("child_process");
const { spawn } = require("child_process");

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

    /**
     * Listener for event sent on camilladsp process termination.
     * The process may terminate either because FIFO has been closed (hence
     * we need to respawn the process immediately) or because of an error.
     * In case of error, we wait with exponential backoff to avoid hogging CPU.
     * If too many consecutive quick respawns occur, stop respawning entirely.
     */
    let listenerClose = function(child, code, signal) {

        // v10 (pi5-beta 2026-09-21): act only on the close of the process that is
        // currently current. processStop() now clears the handle itself, so a stale
        // close arriving after a newer instance spawned must not clobber it -- that
        // would both lose the new handle and trigger a duplicate spawn.
        if (child !== camilla)
            return;

        let timeout = 0;
        let uptime = Date.now() - lastSpawnTime;

        logger.debug("close event");

        // Nullify the camilla process since it has been fully terminated
        camilla = null;

        // .stop() has been called, hence the process is supposed to
        // not to be respawned. Just stop here in case.
        if (run === false) {
            // v10: a DELIBERATE stop is not a failure. Previously this returned without
            // touching the counter, so a stop/start pair -- exactly what FusionDSP's
            // rate change does -- left the failure streak primed and the next early
            // exit was charged as attempt N, giving the 1000/2000/4000 ms ladder that
            // produced the observed ~100 s of silence after a switch.
            consecutiveRespawns = 0;
            return;
        }

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

        let child = spawn(cdPath, args);
        camilla = child;
        lastSpawnTime = Date.now();

     //   logger.info(`camilladsp spawned new process with pid ${child.pid}, instance ${uniqueid}, run: ${run}`);

        //camilla.on("exit", listenerExit);
        child.on("close", (code, signal) => listenerClose(child, code, signal));

    };

    /**
     * Private function to stop camilladsp process. If there is no process running
     * (ie: camilla === null), does not do anything
     */
    let processStop = function() {

        let pid;

        try {

            if (camilla === null)
                return;

            pid = camilla.pid;

            logger.info(`camilladsp stopping service pid ${pid}...`);

            camilla.kill();

            // Hacky way to make this function synchronous
            execSync(`while true; do grep camilladsp /proc/${pid}/cmdline || break; sleep 0.1; done`);

            // v10 (pi5-beta 2026-09-21): clear the handle here, once the OS process is
            // confirmed gone. Previously it stayed non-null until the ASYNC 'close' event
            // fired, so an immediate start() -- exactly what a rate change does -- hit
            // processSpawn's `if (camilla !== null) return;` guard and was silently
            // DEFERRED to the close handler, i.e. delayed by the backoff rather than
            // starting at once.
            camilla = null;

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

