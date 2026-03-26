/**
 * Customer-facing copy for PitBox Kiosk and Sim UI.
 * Single place to update wording for first-time users in a public sim racing lounge.
 */
(function (global) {
    global.KIOSK_COPY = {
        // Instruction screen (phone – no QR params)
        instruction: {
            heading: 'PitBox Kiosk',
            body: 'Scan the QR code on the sim screen to connect your phone.\nThen configure your session on your phone.',
            helper: 'The QR code appears in the top-right of the simulator screen.',
            manualHint: 'Or open this page from the same network with the link shown on the sim.',
        },
        // Claim error
        error: {
            connectionFailed: 'Connection failed.',
            connectionHint: 'Make sure your phone is connected to the same Wi‑Fi as the simulator.',
            tryAgain: 'Try again',
        },
        // Step 1 – Session type
        step1: {
            title: 'Session type',
            desc: 'Choose how you want to drive.',
            solo: 'Solo Drive',
            multiplayer: 'Multiplayer Race',
            create: 'Create Server',
            next: 'Next',
        },
        // Step 2 – Select server (multiplayer)
        step2: {
            title: 'Select Server',
            desc: 'Choose a server to join.',
            loading: 'Loading servers…',
            empty: 'No online servers are currently available.',
            error: 'Could not load servers.',
            retry: 'Retry',
            back: 'Back',
            next: 'Next',
            drivers: 'Drivers',
        },
        // Step 3 – Select car (+ track preview)
        step3: {
            title: 'Select Car',
            desc: 'Choose your car.',
            trackLabel: 'Track',
            back: 'Back',
            next: 'Next',
        },
        // Step 4 – Steering/shifting preset
        step4: {
            title: 'Control preset',
            desc: 'Choose your steering and shifting preset.',
            back: 'Back',
            next: 'Next',
        },
        // Step 5 – Driver name
        step5: {
            title: 'Driver name',
            desc: 'This name may appear in the session.',
            placeholder: 'Your name',
            back: 'Back',
            launch: 'Launch Session',
        },
        // Launching (phone + sim)
        launching: {
            title: 'Launching Simulator',
            subtext: 'Please wait…',
        },
        // Sim idle
        simIdle: {
            heading: 'Waiting for phone connection',
            subtext: 'Scan the QR code in the top-right corner to configure your session.',
            qrLabel: 'QR Code',
        },
        // Results
        results: {
            title: 'FASTEST LAP',
            subtitle: 'Post‑Race Results',
            sessionResults: 'Session Results',
            fastestLap: 'Fastest Lap',
            continue: 'Continue',
        },
        // Global
        global: {
            stepProgress: 'Step {current} of {total}',
            startOver: 'Start Over',
            configureAndLaunch: 'Configure & Launch',
        },
    };

    /**
     * Optional mock server list when backend is not connected.
     * Use in development or when /api/servers returns empty.
     * Shape: [{ id, name, trackName, trackImage?, cars?, currentDrivers?, maxDrivers?, sessionType? }]
     */
    global.KIOSK_MOCK_SERVERS = global.KIOSK_MOCK_SERVERS || [
        { id: 'SERVER_01', name: 'Fastest Lap #1', trackName: 'Lime Rock Park', sessionType: 'Practice', currentDrivers: 3, maxDrivers: 8 },
    ];
})(typeof window !== 'undefined' ? window : this);
