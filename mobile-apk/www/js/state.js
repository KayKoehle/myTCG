export function createAppState() {
    return {
        matchId: `snap-match-${Math.floor(Math.random() * 1_000_000)}`,
        humanPlayerId: 1,
        aiPlayerId: 2,
        // All AI seats (player ids). 1v1: [2]; FFA: [2..n]. The human is
        // always seat 0 / player id 1.
        aiPlayerIds: [2],
        // Local pass-and-play: when non-null, every seat in this list is a
        // human sharing one device (no AI). activeSeatId is the seat currently
        // holding the device — it drives both the controller identity and the
        // viewer perspective, and swaps on a "pass the device" hand-off.
        localSeatIds: null,
        activeSeatId: 1,
        // LAN multiplayer: when true, other seats are remote humans on the
        // network. lanHostBase is the authoritative host's URL (null = we are
        // the host). lanPollTimer polls the host while waiting for a remote turn.
        lanGame: false,
        lanHostBase: null,
        lanPollTimer: null,
        lanPlayerName: null,
        // Reconnect: escalate after repeated poll failures; a backoff timer
        // re-fetches the host's authoritative state until it answers.
        lanReconnecting: false,
        lanReconnectTimer: null,
        lanPollFails: 0,
        seed: Math.floor(Math.random() * 1_000_000_000),
        defaultDeckA: 'epic_of_gilgamesh',
        defaultDeckB: 'siege_of_troy',
        // Set by the main menu's Play flow; they win over the settings-sheet
        // deck dropdowns (which clear them when used).
        deckAName: null,
        deckBName: null,
        deckACards: null,
        // FFA: one deck name per seat (index 0 = the human). Null in 1v1.
        deckNames: null,
        // Menu-started matches carry { deckId, cardIds } for stats/quests.
        statsMeta: null,
        // Elo: the player's rating when the match started, one sampled
        // rating per AI seat (player id -> elo), and the last game's change
        // (for the game-over overlay). Set per match in newGame().
        playerElo: null,
        aiElos: {},
        lastEloDelta: null,
        snapshot: null,
        selectedCardId: null,
        legalPlaySet: new Set(),
        legalMoveChoiceSet: new Set(),
        movableChoiceCardSet: new Set(),
        playableCardSet: new Set(),
        abilityReadyCardSet: new Set(),
        cardNameById: new Map(),
        mulliganSelected: new Set(),
        autoRunning: false,
        actionPending: false,
        dragEndedAt: 0,
        opponentTurnActive: false,
        opponentTurnStartedAt: 0,
        // History-driven animations (round crowns, opponent mulligans) fire on
        // entries added after these markers; reset per match.
        animMatchId: null,
        historySeen: 0,
    };
}

export function buildConfig(ui, app) {
    // In a local pass-and-play game the "you" seat follows whoever holds the
    // device (activeSeatId) and there are no AI seats; otherwise the human is
    // fixed at humanPlayerId and every other seat is AI-driven.
    const localSeats = app.localSeatIds && app.localSeatIds.length ? app.localSeatIds.map(Number) : null;
    const youId = localSeats ? Number(app.activeSeatId ?? localSeats[0]) : app.humanPlayerId;
    // Local hotseat and LAN games have no AI seats — every other seat is a human.
    const noAi = Boolean(localSeats) || Boolean(app.lanGame);
    const aiIds = noAi
        ? []
        : (app.aiPlayerIds && app.aiPlayerIds.length ? app.aiPlayerIds : [app.aiPlayerId]);
    return {
        match_id: app.matchId,
        player_id: youId,
        ai_player_id: app.aiPlayerId,
        ai_player_ids: aiIds,
        viewer_player_id: youId,
        local_seat_ids: localSeats,
        seed: app.seed,
        deck_a: app.deckAName || ui.deckA.value.trim() || app.defaultDeckA,
        deck_b: app.deckBName || ui.deckB.value.trim() || app.defaultDeckB,
        deck_a_cards: app.deckACards || null,
        // FFA: full per-seat deck list; the server prefers it over deck_a/b.
        decks: app.deckNames && app.deckNames.length > 2 ? app.deckNames : null,
        checkpoint_path: ui.checkpointPath.value.trim(),
        device: 'auto',
        // Rated AI opponents: player id -> the Elo the ladder plays at.
        ai_elos: app.aiElos || {},
    };
}
