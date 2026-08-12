import { P2P_HOST_BASE, postJson, lanPost, isLocalBridge, setLanSelfBase, setP2pTransport } from './api.js';
import { closeActiveP2p, createGuestSession, createHostHub, p2pSupported } from './p2p.js';
import { qrSvg } from './qr.js';
import { cardArtTag, cardPngUrl, escapeHtml, showToast } from './helpers.js';
import { createCardRecommender, createCardSearch } from './embedding.js';
import { getQuestBoard, weekendBannerLabel } from './quests.js';
import { createReplayScreens } from './replayview.js';
import {
    BOARDS,
    CARD_BACKS,
    DECK_META,
    DECK_SIZE,
    EMOTES,
    allDeckIds,
    applyCosmetics,
    buyItem,
    cardDeckStats,
    createCustomDeck,
    deckCardIds,
    deckCosmetic,
    deckDisplayName,
    deckEmoteLoadout,
    deckIsEdited,
    deckMatchConfig,
    deckWinRate,
    decksUnlocked,
    deleteCustomDeck,
    equipItem,
    equippedEmoteIds,
    equippedItem,
    toggleEquippedEmote,
    GAME_MODES,
    gameModeById,
    getCrowns,
    getFavoriteMode,
    getSelectedDeckId,
    setFavoriteMode,
    modeUnlocked,
    modeUnlockHint,
    isCustomDeck,
    MAX_ACTIVE_EMOTES,
    ownedCount,
    ownedEmotes,
    ownsItem,
    applyTradeResult,
    renameDeck,
    resetDeck,
    selectDeck,
    setDeckCards,
    setDeckCosmetic,
    setDeckEmoteLoadout,
    shopUnlocked,
    DECK_IDS,
} from './profile.js';

// Companion cards belong together: the collection and the deck grid show them
// as one stack and deck building adds them as a unit.
const COMPANION_GROUPS = [
    ['Gilgamesh', 'Enkidu'],
    ['Achilles', 'Patroclus'],
    ['Kur-Jara', 'Gala-Tura'],
];

function companionPartnerName(cardName) {
    for (const [a, b] of COMPANION_GROUPS) {
        if (cardName === a) return b;
        if (cardName === b) return a;
    }
    return null;
}

// Menu / Decks / Shop screens. The game screen itself stays owned by the
// game controller; this controller only decides which screen is visible and
// starts matches (player's selected deck vs. a random stock AI deck).
export function createMenuController(ui, game, cardStack) {
    // Stock deck lists + full card details, fetched once from /api/collection.
    let stockCardsByDeck = null; // Map deckId -> [card, ...] (deck order)
    let collectionError = null;
    let editorDeckId = null; // deck being edited on the Decks screen
    let shopTab = 'cardBack';
    let confirmingDelete = false; // two-tap guard for deleting a custom deck
    let styleOpen = false; // deck-style section stays collapsed across re-renders
    let recOpen = true; // "Recommended for this deck" starts expanded, stays where the user put it

    // Collection browsing state.
    let searchQuery = '';
    let sortMode = 'name';
    let typeFilter = '';
    let costFilter = '';
    let powerFilter = '';
    let controlsBuilt = false;

    // Pass & Play setup: chosen player count and the deck id picked per seat
    // (index 0 = seat 1). Seat 1 may use any of the player's decks; seats 2+
    // pick from stock decks so their card lists resolve from the catalog.
    let passPlayCount = 2;
    let passPlaySeatDecks = [];
    // Editable display name per seat (index 0 = seat 1). Empty entries fall back
    // to "Player N" so a name is never required.
    let passPlaySeatNames = [];

    // LAN multiplayer setup: discovery + lobby state while the LAN sheet is open.
    let lanEnabled = false;
    // Android runs an in-process HTTP server for LAN; this is the port it bound
    // (advertised to peers so they can reach this device). 0 until started.
    let lanServerPort = 0;
    let lanPeers = [];
    let lanPeersTimer = null;
    let lanLobbyTimer = null;
    // The lobby we're hosting or have joined: { lobby_id, host_base, is_host,
    // my_pid, num_players, seats, started }.
    let lanLobby = null;
    // Invite-code (online) play, while codes are being exchanged:
    // { mode: 'host' | 'guest', session, code, busy, status }. Cleared once the
    // players are connected and the ordinary lobby takes over.
    let p2pView = null;
    // The host's hub, holding one connection per guest. Outlives p2pView, which
    // comes and goes as each extra player is invited.
    let p2pHub = null;
    // Invite-code games seat the same 2-5 players LAN games do.
    const MAX_P2P_SEATS = 5;

    // The Replays library and player (js/replayview.js). Opening one is a
    // navigation like any other, so it goes through pushNav/handleNav.
    const replays = createReplayScreens(ui, {
        cardStack,
        onOpenPlayer: (replayId) => openReplay(replayId),
    });

    // Embedding models, fitted once on the loaded collection.
    let cardSearch = null; // (query) -> [{card, score}] | null
    let recommender = null; // (deckCards, candidates, limit) -> [{card, score}]

    const SHOP_TABS = [
        { kind: 'cardBack', label: 'Card Backs', items: CARD_BACKS },
        { kind: 'emote', label: 'Emotes', items: EMOTES },
        { kind: 'board', label: 'Boards', items: BOARDS },
    ];

    async function ensureCollection() {
        if (stockCardsByDeck) return stockCardsByDeck;
        try {
            const data = await postJson('/api/collection', {});
            stockCardsByDeck = new Map();
            for (const deck of (data.decks || [])) {
                stockCardsByDeck.set(deck.deck_id, deck.cards || []);
            }
            collectionError = null;
            const cards = allCollectionCards();
            cardSearch = createCardSearch(cards);
            recommender = createCardRecommender(cards);
        } catch (error) {
            collectionError = String(error);
        }
        return stockCardsByDeck;
    }

    function stockIds(deckId) {
        const cards = (stockCardsByDeck && stockCardsByDeck.get(deckId)) || [];
        return cards.map((c) => c.id);
    }

    function cardById(cardId) {
        if (!stockCardsByDeck) return null;
        for (const cards of stockCardsByDeck.values()) {
            const found = cards.find((c) => c.id === cardId);
            if (found) return found;
        }
        return null;
    }

    function cardByName(cardName) {
        if (!stockCardsByDeck) return null;
        for (const cards of stockCardsByDeck.values()) {
            const found = cards.find((c) => c.name === cardName);
            if (found) return found;
        }
        return null;
    }

    function allCollectionCards() {
        if (!stockCardsByDeck) return [];
        const all = [];
        for (const deckId of DECK_IDS) {
            all.push(...(stockCardsByDeck.get(deckId) || []));
        }
        return all;
    }

    // --- Screen switching -------------------------------------------------

    // History-based navigation: every screen change pushes a history entry so
    // the Android hardware back button (and the browser's back) walks back
    // through the app — shop -> menu, deck editor -> deck list -> menu — and
    // never straight out of it.
    let navCurrent = { screen: 'menu' };

    function pushNav(state) {
        navCurrent = state;
        try {
            history.pushState(state, '');
        } catch (error) {
            // History API unavailable: navigation still works, back just exits.
        }
    }

    function handleNav(state) {
        // Back closes a floating overlay first (card popup, inspector, sheet)
        // and stays on the current screen.
        const overlay = document.querySelector('.stack-modal.open, .inspector-modal.open, .sheet-modal.open, .choice-modal.open');
        if (overlay) {
            try { history.pushState(navCurrent, ''); } catch (error) { /* ignore */ }
            if (overlay.classList.contains('stack-modal')) {
                cardStack.close();
            } else if (!overlay.classList.contains('choice-modal')) {
                overlay.classList.remove('open');
                overlay.setAttribute('aria-hidden', 'true');
            }
            return;
        }
        const target = state && state.screen ? state : { screen: 'menu' };
        // Backing out of a live match asks for surrender instead of leaving —
        // unless the player hasn't even done the mulligan, then leaving is free.
        if (ui.gameScreen.classList.contains('active') && target.screen !== 'game'
            && game.isMatchLive && game.isMatchLive()
            && !(game.canQuitFree && game.canQuitFree())) {
            pushNav({ screen: 'game' });
            game.promptSurrender();
            return;
        }
        navCurrent = target;
        if (target.screen === 'decks' && decksUnlocked()) {
            editorDeckId = target.editor || null;
            confirmingDelete = false;
            showScreen('decks');
            renderDecks();
            ensureCollection().then(() => renderDecks());
        } else if (target.screen === 'shop' && shopUnlocked()) {
            showScreen('shop');
            renderShop();
        } else if (target.screen === 'replays') {
            replays.renderLibrary();
            showScreen('replays');
        } else if (target.screen === 'replay' && target.replayId) {
            // A replay that no longer loads (deleted, or storage cleared under
            // us) falls back to the library rather than an empty player.
            if (replays.load(target.replayId)) showScreen('replay');
            else { navCurrent = { screen: 'replays' }; replays.renderLibrary(); showScreen('replays'); }
        } else if (target.screen === 'game') {
            showScreen('game');
        } else {
            applyCosmetics();
            renderMenu();
            showScreen('menu');
        }
    }

    function navBack() {
        try {
            history.back();
        } catch (error) {
            handleNav({ screen: 'menu' });
        }
    }

    function showScreen(name) {
        const screens = {
            menu: ui.menuScreen,
            decks: ui.decksScreen,
            shop: ui.shopScreen,
            game: ui.gameScreen,
            replays: ui.replaysScreen,
            replay: ui.replayScreen,
        };
        // Every way off the replay player runs through here, so this is the one
        // place that has to stop auto-play — a timer redrawing a hidden board
        // would otherwise outlive the screen.
        if (name !== 'replay') replays.close();
        for (const el of Object.values(screens)) {
            if (el) el.classList.remove('active');
        }
        if (screens[name]) screens[name].classList.add('active');
    }

    function updateCrowns() {
        const crowns = String(getCrowns());
        if (ui.menuCrowns) ui.menuCrowns.textContent = crowns;
        if (ui.shopCrowns) ui.shopCrowns.textContent = crowns;
    }

    // --- Main menu -----------------------------------------------------------

    // The art shown for a deck: the stock signature card, or a custom deck's
    // first card (once the collection is loaded).
    function deckArtCardName(deckId) {
        if (DECK_META[deckId]) return DECK_META[deckId].mainCard;
        const firstId = deckCardIds(deckId, stockIds(deckId))[0];
        const card = firstId ? cardById(firstId) : null;
        return card ? card.name : null;
    }

    function deckArtHtml(deckId) {
        const mainCard = deckArtCardName(deckId);
        if (!mainCard) return '<div class="deck-art-empty">?</div>';
        return `<img src="${cardPngUrl(mainCard)}" alt="${escapeHtml(mainCard)}" draggable="false" loading="lazy" onerror="this.style.display='none';">`;
    }

    // Favorite game mode: the Play button always launches it directly; the
    // chip row underneath changes (and persists) the favorite.
    function renderModeRow() {
        if (!ui.menuModeRow) return;
        const favorite = getFavoriteMode();
        ui.menuModeRow.innerHTML = GAME_MODES.map((mode) => {
            const locked = !modeUnlocked(mode.id);
            return `
            <button class="mode-chip ${mode.id === favorite ? 'active' : ''} ${locked ? 'locked' : ''}" role="radio"
                aria-checked="${mode.id === favorite}" data-mode-id="${mode.id}"
                title="${escapeHtml(locked ? (modeUnlockHint(mode.id) || mode.name) : mode.name)}">${locked ? '🔒 ' : ''}${escapeHtml(mode.label)}</button>
        `;
        }).join('');
        ui.menuModeRow.querySelectorAll('[data-mode-id]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const modeId = btn.dataset.modeId;
                if (!modeUnlocked(modeId)) {
                    showToast(modeUnlockHint(modeId));
                    return;
                }
                setFavoriteMode(modeId);
                renderMenu();
            });
        });
        if (ui.menuPlaySub) {
            const mode = gameModeById(favorite);
            ui.menuPlaySub.textContent = mode.sub;
        }
    }

    function renderMenu() {
        updateCrowns();
        const deckId = getSelectedDeckId();
        // Only rewrite the art when it actually changed — recreating the <img>
        // on every re-render (e.g. tapping a mode chip) makes it flicker.
        const artCard = deckArtCardName(deckId) || '';
        if (ui.menuDeckArt.dataset.artCard !== artCard) {
            ui.menuDeckArt.dataset.artCard = artCard;
            ui.menuDeckArt.innerHTML = deckArtHtml(deckId);
        }
        ui.menuDeckName.textContent = deckDisplayName(deckId);
        renderModeRow();

        const decksLocked = !decksUnlocked();
        const shopLocked = !shopUnlocked();
        ui.btnMenuDecks.classList.toggle('locked', decksLocked);
        ui.btnMenuShop.classList.toggle('locked', shopLocked);
        if (ui.menuDecksLock) ui.menuDecksLock.classList.toggle('hidden', !decksLocked);
        if (ui.menuShopLock) ui.menuShopLock.classList.toggle('hidden', !shopLocked);

        updateReconnectButton();
        renderQuestPanel();
    }

    // Offer a one-tap rejoin when an unclean exit (app killed, crash) left a
    // live LAN guest session behind. Hidden whenever there's nothing to rejoin.
    function updateReconnectButton() {
        if (!ui.btnReconnect) return;
        const session = game.loadLanSession && game.loadLanSession();
        ui.btnReconnect.classList.toggle('hidden', !session);
        if (session && ui.reconnectSub) {
            const host = session.hostBase.replace(/^https?:\/\//, '');
            ui.reconnectSub.textContent = `Rejoin your game on ${host}`;
        }
    }

    async function reconnectSavedLan() {
        const session = game.loadLanSession && game.loadLanSession();
        if (!session) { renderMenu(); return; }
        applyCosmetics(getSelectedDeckId());
        pushNav({ screen: 'game' });
        showScreen('game');
        // startLanGame re-fetches the host's authoritative state; if the host is
        // unreachable it drops into the in-game reconnect overlay rather than
        // failing outright.
        game.startLanGame({
            hostBase: session.hostBase,
            matchId: session.matchId,
            seed: session.seed,
            playerId: session.playerId,
            decks: session.decks,
        });
    }

    function openMenu() {
        // Back on the menu the default cosmetics apply again (a match may have
        // switched to a deck's own card back / board).
        applyCosmetics();
        renderMenu();
        showScreen('menu');
    }

    // --- Quests ----------------------------------------------------------------

    function questRowHtml(item) {
        const pct = Math.round(Math.min(1, item.progress / item.def.target) * 100);
        return `
            <div class="quest-row">
                <div class="quest-row-main">
                    <div class="quest-title">${escapeHtml(item.def.title)}</div>
                    <div class="quest-bar"><div class="quest-bar-fill" style="width:${pct}%"></div></div>
                </div>
                <div class="quest-side">
                    <span class="quest-progress">${item.progress}/${item.def.target}</span>
                    <span class="quest-reward">${item.def.reward}<span class="crown-icon"></span></span>
                </div>
            </div>
        `;
    }

    // "new in 4h 32m" — how long until the next quest rotation.
    function countdownText(ms) {
        const totalMinutes = Math.max(1, Math.ceil(ms / 60000));
        const days = Math.floor(totalMinutes / 1440);
        const hours = Math.floor((totalMinutes % 1440) / 60);
        const minutes = totalMinutes % 60;
        if (days > 0) return `${days}d ${hours}h`;
        if (hours > 0) return `${hours}h ${minutes}m`;
        return `${minutes}m`;
    }

    function renderQuestPanel() {
        if (!ui.menuQuests) return;
        const board = getQuestBoard();
        ui.menuQuests.classList.remove('hidden');
        if (!board.unlocked) {
            // Locked panels look like the locked Decks/Shop tiles.
            ui.menuQuests.innerHTML = `
                <div class="quest-section quest-locked">🔒 Play 3 games to unlock quests</div>
            `;
            return;
        }
        const { def: eventDef, active: eventActive } = board.weekend;
        ui.menuQuests.innerHTML = `
            <div class="weekend-banner ${eventActive ? 'live' : ''}">
                <div class="weekend-head">
                    <span class="weekend-label">${escapeHtml(weekendBannerLabel())}</span>
                    <span class="weekend-name">${escapeHtml(eventDef.name)}</span>
                </div>
                <div class="weekend-desc">${escapeHtml(eventDef.desc)}</div>
            </div>
            <div class="quest-section">
                <div class="quest-section-title">Quests
                    <span class="quest-countdown">${board.nextQuestWaiting
                        ? 'next quest waiting — complete one!'
                        : `new quest in ${countdownText(board.nextQuestMs)}`}</span></div>
                ${board.rolling.map((item) => questRowHtml(item)).join('')}
                ${board.rolling.length ? '' : '<div class="quest-empty">All quests done — the next one is on its way.</div>'}
            </div>
            <div class="quest-section">
                <div class="quest-section-title">Weekly quests
                    <span class="quest-countdown">new in ${countdownText(board.weeklyResetMs)}</span></div>
                ${board.weekly.map((item) => questRowHtml(item)).join('')}
                ${board.weekly.length ? '' : '<div class="quest-empty">All weekly quests complete — well fought!</div>'}
            </div>
        `;
    }

    // --- Play ---------------------------------------------------------------------

    async function play() {
        await ensureCollection();
        const deckId = getSelectedDeckId();
        const ids = deckCardIds(deckId, stockIds(deckId));
        if (ids.length !== DECK_SIZE) {
            showToast(`"${deckDisplayName(deckId)}" needs exactly ${DECK_SIZE} cards (it has ${ids.length}).`);
            openDecks();
            return;
        }
        const deckA = deckMatchConfig(deckId, stockIds(deckId));
        // Every AI rival draws a random stock deck. Mirror matches are fine:
        // the engine aliases card ids shared between seats.
        const mode = gameModeById(getFavoriteMode());
        if (!modeUnlocked(mode.id)) {
            showToast(modeUnlockHint(mode.id));
            return;
        }
        const aiDecks = Array.from(
            { length: Math.max(1, mode.players - 1) },
            () => DECK_IDS[Math.floor(Math.random() * DECK_IDS.length)]
        );
        applyCosmetics(deckId); // this deck's card back / board / emotes
        pushNav({ screen: 'game' });
        showScreen('game');
        await game.startGame({
            deckAName: deckA.name,
            deckACards: deckA.cards,
            deckBName: aiDecks[0],
            decks: mode.players > 2 ? [deckA.name, ...aiDecks] : null,
            statsMeta: { deckId, cardIds: ids, mode: mode.id },
        });
    }

    // --- Pass & Play (local hotseat) ----------------------------------------

    const PASSPLAY_MIN = 2;
    const PASSPLAY_MAX = 5;

    // Default deck for a seat: seat 1 follows the player's current selection;
    // later seats get distinct stock decks so a fresh table isn't a mirror.
    function defaultSeatDeck(seatIdx) {
        if (seatIdx === 0) return getSelectedDeckId();
        return DECK_IDS[(seatIdx - 1) % DECK_IDS.length];
    }

    // Deck ids a seat may pick from: seat 1 can use custom decks too; seats 2+
    // are limited to stock decks (their cards must resolve from the catalog).
    function seatDeckChoices(seatIdx) {
        return seatIdx === 0 ? allDeckIds() : DECK_IDS.slice();
    }

    async function openPassPlay() {
        await ensureCollection();
        // Seed/repair per-seat deck picks up to the current count.
        for (let i = 0; i < PASSPLAY_MAX; i += 1) {
            const choices = seatDeckChoices(i);
            if (!passPlaySeatDecks[i] || !choices.includes(passPlaySeatDecks[i])) {
                passPlaySeatDecks[i] = defaultSeatDeck(i);
            }
        }
        ui.passPlayModal.classList.add('open');
        ui.passPlayModal.setAttribute('aria-hidden', 'false');
        renderPassPlay();
    }

    function closePassPlay() {
        ui.passPlayModal.classList.remove('open');
        ui.passPlayModal.setAttribute('aria-hidden', 'true');
    }

    function renderPassPlay() {
        ui.passPlayCount.innerHTML = '';
        for (let n = PASSPLAY_MIN; n <= PASSPLAY_MAX; n += 1) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = `passplay-count-chip ${n === passPlayCount ? 'active' : ''}`;
            btn.setAttribute('role', 'radio');
            btn.setAttribute('aria-checked', String(n === passPlayCount));
            btn.textContent = `${n}P`;
            btn.addEventListener('click', () => {
                passPlayCount = n;
                renderPassPlay();
            });
            ui.passPlayCount.appendChild(btn);
        }

        ui.passPlaySeats.innerHTML = '';
        for (let i = 0; i < passPlayCount; i += 1) {
            const row = document.createElement('div');
            row.className = 'passplay-seat';
            const name = document.createElement('input');
            name.type = 'text';
            name.className = 'passplay-seat-name';
            name.maxLength = 20;
            name.placeholder = `Player ${i + 1}`;
            name.value = passPlaySeatNames[i] || '';
            name.setAttribute('aria-label', `Name for player ${i + 1}`);
            name.addEventListener('input', () => { passPlaySeatNames[i] = name.value; });
            const select = document.createElement('select');
            select.className = 'passplay-deck';
            for (const deckId of seatDeckChoices(i)) {
                const opt = document.createElement('option');
                opt.value = deckId;
                opt.textContent = deckDisplayName(deckId);
                if (deckId === passPlaySeatDecks[i]) opt.selected = true;
                select.appendChild(opt);
            }
            select.addEventListener('change', () => { passPlaySeatDecks[i] = select.value; });
            row.appendChild(name);
            row.appendChild(select);
            ui.passPlaySeats.appendChild(row);
        }
    }

    function startPassPlay() {
        const count = passPlayCount;
        const seatIds = passPlaySeatDecks.slice(0, count);
        // Seat 1 may be a custom/edited deck (its cards ride along as deck_a_cards);
        // seats 2+ are stock names the server resolves from the catalog.
        const seat0 = deckMatchConfig(seatIds[0], stockIds(seatIds[0]));
        const seat0Ids = deckCardIds(seatIds[0], stockIds(seatIds[0]));
        if (seat0Ids.length !== DECK_SIZE) {
            showToast(`"${deckDisplayName(seatIds[0])}" needs exactly ${DECK_SIZE} cards (it has ${seat0Ids.length}).`);
            return;
        }
        const names = [seat0.name, ...seatIds.slice(1)];
        const localSeatIds = Array.from({ length: count }, (_, i) => i + 1);
        // Player display names for the hand-off overlay, history, and score
        // panel. Blank entries default to "Player N" at render time.
        const localSeatNames = Array.from({ length: count }, (_, i) => {
            const entered = (passPlaySeatNames[i] || '').trim();
            return entered || `Player ${i + 1}`;
        });
        applyCosmetics(seatIds[0]);
        closePassPlay();
        pushNav({ screen: 'game' });
        showScreen('game');
        game.startGame({
            deckAName: seat0.name,
            deckACards: seat0.cards,
            deckBName: names[1],
            decks: count > 2 ? names : null,
            localSeatIds,
            localSeatNames,
        });
    }

    // --- LAN multiplayer -----------------------------------------------------

    function lanSelfPort() {
        // On Android the reachable port is the in-process server's, not the
        // Capacitor origin's (which has no server behind it).
        if (lanServerPort) return lanServerPort;
        return Number(window.location.port) || (window.location.protocol === 'https:' ? 443 : 80);
    }

    // On Android, boot the in-process HTTP + discovery server the native bridge
    // runs and point self LAN calls at it. Returns false if it could not start.
    // A no-op (returns true) on browser/desktop, which use same-origin.
    async function ensureLanServer() {
        if (!isLocalBridge()) return true; // browser/desktop use the same-origin server
        if (!window.MyTCGLocalApi.startLanServer) {
            showToast('Update the app to the latest version to use LAN play.');
            return false;
        }
        // Called every time the LAN screen opens (not just once): the native side
        // is idempotent about the server but re-takes the multicast lock that
        // pauseLanDiscovery released on the previous close.
        try {
            const data = JSON.parse(window.MyTCGLocalApi.startLanServer());
            if (!data.ok || !data.port) {
                showToast(data.error || 'Could not start LAN networking.');
                return false;
            }
            lanServerPort = Number(data.port);
            setLanSelfBase(data.base);
            return true;
        } catch (error) {
            showToast(`Could not start LAN networking: ${error}`);
            return false;
        }
    }

    // Drop the discovery (multicast) lock to save battery once we stop browsing
    // for peers — leaving the LAN screen, or heading into a match. A hosted game
    // stays reachable over TCP without it.
    function pauseLanDiscovery() {
        if (isLocalBridge() && window.MyTCGLocalApi.pauseLanDiscovery) {
            try { window.MyTCGLocalApi.pauseLanDiscovery(); } catch (error) { /* best-effort */ }
        }
    }
    function lanPlayerName() {
        return (localStorage.getItem('mytcg_lan_name') || '').trim() || 'Player';
    }
    // Deck config for whichever deck the player picked for LAN. Custom/edited
    // decks ride along as an explicit card list so the host can register them.
    function lanDeckConfig() {
        // LAN reuses whichever deck is selected for vs-AI play — no separate picker.
        const deckId = getSelectedDeckId();
        const cfg = deckMatchConfig(deckId, stockIds(deckId));
        const ids = deckCardIds(deckId, stockIds(deckId));
        return { deckId, name: cfg.name, cards: cfg.cards, size: ids.length };
    }

    async function openLan() {
        // LAN play is peer-to-peer over HTTP: a host advertises on the network
        // and guests reach its API directly. The Android build has no FastAPI
        // server, so it boots an equivalent in-process one (Python, via the
        // native bridge) first; if that fails there is nothing to host with.
        if (isLocalBridge() && !(await ensureLanServer())) return;
        await ensureCollection();
        ui.lanModal.classList.add('open');
        ui.lanModal.setAttribute('aria-hidden', 'false');
        renderLan();
        try {
            await lanPost('', '/api/lan/enable', { name: lanPlayerName(), port: lanSelfPort() });
            lanEnabled = true;
        } catch (error) {
            showToast(`Could not start LAN discovery: ${error}`);
        }
        renderLan();
        startPeerPolling();
    }

    function closeLan({ keepDiscovery = false } = {}) {
        stopPeerPolling();
        stopLobbyPolling();
        // Half-finished code swap: nobody is connected yet, so drop it rather
        // than leaving a peer connection open behind a closed sheet. A live
        // lobby (including the one beginLanMatch closes this sheet for) stays.
        if (p2pView && !lanLobby) {
            if (p2pView.session) p2pView.session.close();
            p2pView = null;
        }
        ui.lanModal.classList.remove('open');
        ui.lanModal.setAttribute('aria-hidden', 'true');
        if (!keepDiscovery && lanEnabled && !lanLobby) {
            lanPost('', '/api/lan/disable', {}).catch(() => {});
            lanEnabled = false;
        }
        // Either way we've stopped browsing for peers, so drop the battery-hungry
        // discovery lock (Android). keepDiscovery keeps the LanService running
        // for the host's live match; it does not need multicast reception.
        pauseLanDiscovery();
    }

    function startPeerPolling() {
        stopPeerPolling();
        const tick = async () => {
            try {
                const data = await lanPost('', '/api/lan/peers', {});
                lanPeers = data.peers || [];
                if (!lanLobby) renderLanPeers();
            } catch (error) { /* transient; keep polling */ }
            lanPeersTimer = setTimeout(tick, 2000);
        };
        tick();
    }
    function stopPeerPolling() {
        if (lanPeersTimer) { clearTimeout(lanPeersTimer); lanPeersTimer = null; }
    }

    async function hostLan() {
        const deck = lanDeckConfig();
        if (deck.size !== DECK_SIZE) {
            showToast(`"${deckDisplayName(deck.deckId)}" needs exactly ${DECK_SIZE} cards.`);
            return;
        }
        try {
            // No headcount is sent: the lobby stays open and the match starts
            // with whoever has joined (>= 2 players) when the host is ready.
            const data = await lanPost('', '/api/lan/host', {
                name: lanPlayerName(),
                deck_name: deck.name,
                deck_cards: deck.cards,
            });
            const lobby = data.lobby;
            lanLobby = {
                lobby_id: lobby.lobby_id, host_base: '', is_host: true, my_pid: 1,
                num_players: lobby.num_players, seats: lobby.seats, started: false,
            };
            stopPeerPolling();
            startLobbyPolling();
            renderLan();
        } catch (error) {
            showToast(`Could not host: ${error}`);
        }
    }

    async function joinLan(peer) {
        const lobby = peer.lobby;
        if (!lobby) return;
        const deck = lanDeckConfig();
        if (deck.size !== DECK_SIZE) {
            showToast(`"${deckDisplayName(deck.deckId)}" needs exactly ${DECK_SIZE} cards.`);
            return;
        }
        try {
            const data = await lanPost(peer.address, '/api/lan/join', {
                lobby_id: lobby.lobby_id,
                name: lanPlayerName(),
                deck_name: deck.name,
                deck_cards: deck.cards,
            });
            if (!data.ok) { showToast(data.error || 'Join failed'); return; }
            lanLobby = {
                lobby_id: lobby.lobby_id, host_base: peer.address, is_host: false,
                my_pid: data.player_id, num_players: lobby.num_players,
                seats: (data.lobby && data.lobby.seats) || [], started: false,
            };
            stopPeerPolling();
            startLobbyPolling();
            renderLan();
        } catch (error) {
            showToast(`Could not join: ${error}`);
        }
    }

    function startLobbyPolling() {
        stopLobbyPolling();
        const tick = async () => {
            try {
                const data = await lanPost(lanLobby.host_base, '/api/lan/lobby', { lobby_id: lanLobby.lobby_id });
                if (data.ok && data.lobby) {
                    lanLobby.seats = data.lobby.seats;
                    lanLobby.started = data.lobby.started;
                    // Not while a code swap is on screen: inviting the third,
                    // fourth and fifth player happens *from* the lobby, so this
                    // tick would otherwise rebuild the panel — and wipe the box
                    // — every 1.5s while the host pastes the reply code into it.
                    // The panel re-renders itself when the swap ends.
                    if (!p2pView) renderLan();
                    // A guest jumps into the match as soon as the host starts it.
                    if (data.lobby.started && !lanLobby.is_host) {
                        beginLanMatch({
                            hostBase: lanLobby.host_base,
                            matchId: data.lobby.match_id,
                            // A LAN guest never learns the seed (the host owns
                            // the deal). An invite-code guest agreed it by
                            // commit-reveal, so it carries into the match — and
                            // from there into the guest's own replay, where a
                            // deal from any other seed would show up.
                            seed: lanLobby.agreed_seed || 0,
                            playerId: lanLobby.my_pid,
                            decks: null,
                        });
                        return;
                    }
                }
            } catch (error) { /* transient; keep polling */ }
            lanLobbyTimer = setTimeout(tick, 1500);
        };
        tick();
    }
    function stopLobbyPolling() {
        if (lanLobbyTimer) { clearTimeout(lanLobbyTimer); lanLobbyTimer = null; }
    }

    async function startLanAsHost() {
        try {
            // An invite-code game settles its deal here, with every player
            // seated: each has already committed to a nonce, so nobody — host
            // included — can pick a shuffle that suits them (js/p2p.js).
            let seed;
            if (p2pHub) {
                setP2pStatus('Agreeing the shuffle with every player…');
                seed = await p2pHub.agreeSeed();
            }
            const data = await lanPost('', '/api/lan/start', {
                lobby_id: lanLobby.lobby_id, ...(seed === undefined ? {} : { seed }),
            });
            if (!data.ok) { showToast(data.error || 'Could not start'); return; }
            beginLanMatch({
                hostBase: null, matchId: data.match_id, seed: data.seed,
                playerId: 1, decks: data.decks,
            });
        } catch (error) {
            showToast(`Could not start: ${error.message || error}`);
            renderLan();
        }
    }

    // --- Invite-code (online) play -------------------------------------------
    // Same lobby, same match, same trading as LAN — the two differ only in how
    // the players reach each other: a direct WebRTC channel rather than a LAN
    // address, so no server of ours sits in the middle (js/p2p.js). The host
    // runs one connection per guest, so free-for-alls work like duels with more
    // invites.

    // What a guest may ask its host to run. A guest is a stranger's browser, so
    // it gets the calls a player legitimately needs and nothing else — notably
    // not /api/lan/start (the host decides when to start) and not the sandbox
    // routes, which can edit a live match at will.
    const P2P_GUEST_PATHS = new Set([
        '/api/state', '/api/action', '/api/ai-move', '/api/replay',
        '/api/lan/join', '/api/lan/lobby',
        '/api/lan/trade/propose', '/api/lan/trade/offer', '/api/lan/trade/confirm',
        '/api/lan/trade/cancel', '/api/lan/trade/state',
    ]);

    // Host side: run one of a guest's calls against our own local server.
    function p2pServe(path, body) {
        if (!P2P_GUEST_PATHS.has(path)) {
            return Promise.reject(new Error(`The host refused the call ${path}.`));
        }
        return lanPost('', path, body);
    }

    function setP2pStatus(text) {
        const node = document.querySelector('.p2p-status');
        if (node) node.textContent = text || '';
    }

    function p2pDeckOrWarn() {
        const deck = lanDeckConfig();
        if (deck.size !== DECK_SIZE) {
            showToast(`"${deckDisplayName(deck.deckId)}" needs exactly ${DECK_SIZE} cards.`);
            return null;
        }
        if (!p2pSupported()) {
            showToast('This browser cannot make direct connections. Invite-code play needs '
                + 'WebRTC over a secure origin (https, or localhost).');
            return null;
        }
        return deck;
    }

    // Open (or reuse) the lobby this host's guests join as they connect. The
    // seed passed here is a placeholder: the real one is agreed at start.
    async function ensureP2pLobby(deck) {
        if (lanLobby && lanLobby.is_host) return lanLobby.lobby_id;
        const data = await lanPost('', '/api/lan/host', {
            name: lanPlayerName(),
            deck_name: deck.name,
            deck_cards: deck.cards,
            num_players: MAX_P2P_SEATS,
        });
        if (!data.ok) throw new Error(data.error || 'Could not open the lobby.');
        lanLobby = {
            lobby_id: data.lobby.lobby_id, host_base: '', is_host: true, my_pid: 1,
            num_players: MAX_P2P_SEATS, seats: data.lobby.seats, started: false, p2p: true,
        };
        return lanLobby.lobby_id;
    }

    /** Host: mint the next invite code (the first one also creates the hub). */
    async function startP2pHost() {
        const deck = p2pDeckOrWarn();
        if (!deck) return;
        p2pView = { mode: 'host', busy: true, status: 'Preparing your invite…' };
        renderLan();
        try {
            if (!p2pHub) p2pHub = await createHostHub({ name: lanPlayerName() });
            const code = await p2pHub.createInvite();
            p2pView = { mode: 'host', code, busy: false, status: '' };
        } catch (error) {
            p2pView = null;
            showToast(`Could not create an invite: ${error.message || error}`);
        }
        renderLan();
    }

    /** Host: link up with a guest's reply and seat them. */
    async function connectP2pHost(replyCode) {
        const view = p2pView;
        if (!view || !p2pHub) return;
        const deck = p2pDeckOrWarn();
        if (!deck) return;
        view.busy = true;
        view.status = 'Connecting…';
        renderLan();
        try {
            const { guestName, seat } = await p2pHub.acceptReply(replyCode);
            const lobbyId = await ensureP2pLobby(deck);
            // Answer their calls before telling them where to join.
            p2pHub.serve(p2pServe);
            p2pHub.tell(seat, { t: 'lobby', lobby_id: lobbyId });
            p2pView = null;
            stopPeerPolling();
            startLobbyPolling();
            showToast(`${guestName} connected.`);
        } catch (error) {
            if (p2pView === view) {
                view.busy = false;
                view.status = String(error.message || error);
            }
        }
        renderLan();
    }

    /** Guest: answer an invite, hand back a reply code, then wait for the host. */
    async function startP2pGuest(inviteCode) {
        const deck = p2pDeckOrWarn();
        if (!deck) return;
        let session;
        try {
            session = await createGuestSession({ inviteCode, name: lanPlayerName() });
        } catch (error) {
            showToast(String(error.message || error));
            return;
        }
        const view = { mode: 'guest', session, code: session.replyCode, busy: true, status: '' };
        p2pView = view;
        renderLan();
        // The seed lands when the host starts; keep it for the guest's own
        // replay, where a deal from any other seed would show up.
        session.onAgreedSeed((seed, error) => {
            if (error) {
                showToast(String(error.message || error));
                leaveLanLobby();
                return;
            }
            if (lanLobby) lanLobby.agreed_seed = seed;
        });
        try {
            const { lobbyId, request } = await session.begin();
            setP2pTransport(request);
            const data = await lanPost(P2P_HOST_BASE, '/api/lan/join', {
                lobby_id: lobbyId,
                name: lanPlayerName(),
                deck_name: deck.name,
                deck_cards: deck.cards,
            });
            if (!data.ok) throw new Error(data.error || 'Join failed.');
            lanLobby = {
                lobby_id: lobbyId, host_base: P2P_HOST_BASE, is_host: false,
                my_pid: data.player_id, num_players: MAX_P2P_SEATS,
                seats: (data.lobby && data.lobby.seats) || [], started: false, p2p: true,
            };
            p2pView = null;
            stopPeerPolling();
            startLobbyPolling();
        } catch (error) {
            setP2pTransport(null);
            closeActiveP2p();
            if (p2pView === view) {
                view.busy = false;
                view.status = String(error.message || error);
            }
        }
        renderLan();
    }

    function cancelP2p() {
        if (p2pView && p2pView.mode === 'guest' && p2pView.session) p2pView.session.close();
        p2pView = null;
        // A host cancelling a *second* invite keeps the game it already has.
        if (!lanLobby) {
            closeActiveP2p();
            setP2pTransport(null);
            p2pHub = null;
        }
        renderLan();
    }

    async function copyToClipboard(text) {
        try {
            await navigator.clipboard.writeText(text);
            showToast('Code copied.');
        } catch (error) {
            showToast('Could not copy automatically — select the code and copy it by hand.');
        }
    }

    // --- QR scanning ---------------------------------------------------------
    // Reading a code back is the half that needs a camera. BarcodeDetector is
    // built into Chrome on Android (where phone-to-phone actually happens) but
    // missing on most desktops, so the button only appears where it works —
    // pasting is always available.

    function scanSupported() {
        return typeof BarcodeDetector !== 'undefined'
            && Boolean(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    }

    async function scanQrCode(onCode) {
        if (!scanSupported()) { showToast('This device cannot scan codes — paste it instead.'); return; }
        const overlay = document.createElement('div');
        overlay.className = 'p2p-scan';
        overlay.innerHTML = `
            <video class="p2p-scan-video" playsinline muted></video>
            <p class="tiny">Point the camera at the other player's code.</p>
            <button class="btn ghost" type="button">Cancel</button>`;
        document.body.appendChild(overlay);
        const video = overlay.querySelector('video');
        let stream = null;
        let stopped = false;
        const stop = () => {
            stopped = true;
            if (stream) stream.getTracks().forEach((track) => track.stop());
            overlay.remove();
        };
        overlay.querySelector('button').addEventListener('click', stop);
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: 'environment' }, audio: false,
            });
            video.srcObject = stream;
            await video.play();
            const detector = new BarcodeDetector({ formats: ['qr_code'] });
            const tick = async () => {
                if (stopped) return;
                try {
                    const found = await detector.detect(video);
                    const hit = found.find((code) => /^MYTCG\d/.test(code.rawValue || ''));
                    if (hit) { stop(); onCode(hit.rawValue); return; }
                } catch (error) { /* a frame failed to decode; try the next */ }
                requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);
        } catch (error) {
            stop();
            showToast(`Could not open the camera: ${error.message || error}`);
        }
    }

    function beginLanMatch({ hostBase, matchId, seed, playerId, decks }) {
        const lobby = lanLobby;
        closeLan({ keepDiscovery: true });
        lanLobby = lobby; // keep for the in-game trade UI (rosters/host base)
        applyCosmetics(getSelectedDeckId());
        pushNav({ screen: 'game' });
        showScreen('game');
        game.startLanGame({ hostBase, matchId, seed, playerId, decks });
    }

    function renderLan() {
        if (!ui.lanBody) return;
        const name = lanPlayerName();
        // Exchanging invite codes: the panel is a code to send and a box to
        // paste the other side's answer into.
        if (p2pView) {
            renderP2pPanel();
            return;
        }
        // In a lobby: waiting room.
        if (lanLobby) {
            const seats = lanLobby.seats || [];
            const rows = seats.map((s) => `
                <div class="lan-seat"><span>${escapeHtml(s.name)}</span>
                    <span class="tiny">seat ${s.player_id}</span></div>`).join('');
            // The lobby has no fixed size: show a single "waiting" hint while
            // there's still room, rather than padding to a chosen headcount.
            const hasRoom = seats.length < (lanLobby.num_players || 5);
            const waitingRow = hasRoom
                ? '<div class="lan-seat lan-seat-empty"><span class="tiny">waiting for players to join…</span></div>'
                : '';
            const canStart = lanLobby.is_host && seats.length >= 2;
            const startLabel = seats.length >= 2 ? `Start ${seats.length}-player game` : 'Need at least 2 players';
            // An invite-code host adds each further player with another code
            // swap; LAN guests just walk in, so the button is p2p-only.
            const inviteMore = lanLobby.is_host && p2pHub && seats.length < MAX_P2P_SEATS
                ? `<button class="btn ghost" id="lanInviteMoreBtn" style="width:100%;margin-top:8px;">
                        Invite another player (${seats.length}/${MAX_P2P_SEATS} seated)</button>`
                : '';
            ui.lanBody.innerHTML = `
                <div class="lan-section-title">${lanLobby.is_host ? 'Your lobby' : 'Joined lobby'}
                    (${seats.length} in)</div>
                <div class="lan-seats">${rows}${waitingRow}</div>
                ${lanLobby.is_host
                    ? `<button class="btn" id="lanStartBtn" ${canStart ? '' : 'disabled'} style="width:100%;margin-top:12px;">
                            ${startLabel}</button>
                       <p class="tiny" style="margin-top:8px;">The game starts with everyone in the lobby. Start whenever you're ready.</p>`
                    : '<p class="tiny" style="margin-top:12px;">Waiting for the host to start…</p>'}
                ${inviteMore}
                <button class="btn ghost" id="lanLeaveBtn" style="width:100%;margin-top:8px;">Leave lobby</button>`;
            const startBtn = document.getElementById('lanStartBtn');
            if (startBtn) startBtn.addEventListener('click', startLanAsHost);
            const inviteMoreBtn = document.getElementById('lanInviteMoreBtn');
            if (inviteMoreBtn) inviteMoreBtn.addEventListener('click', startP2pHost);
            document.getElementById('lanLeaveBtn').addEventListener('click', leaveLanLobby);
            return;
        }
        // Otherwise: name + host/join browser. LAN uses the deck already selected
        // for vs-AI play, so there is no separate deck picker here.
        ui.lanBody.innerHTML = `
            <label class="lan-label">Your name</label>
            <input id="lanNameInput" class="lan-input" value="${escapeHtml(name)}" maxlength="20" />
            <div class="lan-section-title">Host a game</div>
            <p class="tiny" style="margin:0 0 8px;">Open a lobby and start once others join — 2 to 5 players.</p>
            <button class="btn" id="lanHostBtn" style="width:100%;">Host game</button>
            <div class="lan-section-title">Join a game ${lanEnabled ? '' : '(starting discovery…)'}</div>
            <div class="lan-peers" id="lanPeers"></div>
            <div class="lan-section-title">Play online with a friend</div>
            <p class="tiny" style="margin:0 0 8px;">Anywhere, not just this Wi‑Fi. Swap a code over chat
                and the game connects you directly — no server in between. One code swap per guest,
                so a free-for-all seats up to ${MAX_P2P_SEATS} the same way a duel seats two.</p>
            <button class="btn ghost" id="p2pHostBtn" style="width:100%;">Create an invite code</button>
            <label class="lan-label" for="p2pInviteInput">…or enter a friend's invite code</label>
            <textarea id="p2pInviteInput" class="lan-input p2p-code" rows="3"
                placeholder="MYTCG2.…"></textarea>
            ${scanSupported()
                ? '<button class="btn ghost" id="p2pScanJoinBtn" style="width:100%;margin-top:8px;">Scan their code</button>'
                : ''}
            <button class="btn ghost" id="p2pJoinBtn" style="width:100%;margin-top:8px;">Answer this invite</button>`;
        const nameInput = document.getElementById('lanNameInput');
        // Persist on every keystroke so a later re-render never loses in-progress text.
        nameInput.addEventListener('input', () => localStorage.setItem('mytcg_lan_name', nameInput.value.trim()));
        document.getElementById('lanHostBtn').addEventListener('click', hostLan);
        document.getElementById('p2pHostBtn').addEventListener('click', startP2pHost);
        document.getElementById('p2pJoinBtn').addEventListener('click', () => {
            const code = document.getElementById('p2pInviteInput').value;
            if (!code.trim()) { showToast('Enter the invite code your friend sent you.'); return; }
            startP2pGuest(code);
        });
        const scanJoinBtn = document.getElementById('p2pScanJoinBtn');
        if (scanJoinBtn) scanJoinBtn.addEventListener('click', () => scanQrCode(startP2pGuest));
        renderLanPeers();
    }

    // The code-swapping panel, for both sides: the code we produced — as text to
    // paste and as a QR to point a camera at — and, for the host, a box for the
    // reply that comes back.
    function renderP2pPanel() {
        const view = p2pView;
        const isHost = view.mode === 'host';
        if (view.busy && !view.code) {
            ui.lanBody.innerHTML = `
                <div class="lan-section-title">Play online</div>
                <p class="tiny">${escapeHtml(view.status || 'Working…')}</p>
                <button class="btn ghost" id="p2pCancelBtn" style="width:100%;margin-top:12px;">Cancel</button>`;
            document.getElementById('p2pCancelBtn').addEventListener('click', cancelP2p);
            return;
        }
        const seated = lanLobby ? (lanLobby.seats || []).length : 1;
        const title = isHost
            ? (seated > 1 ? `Invite player ${seated + 1}` : 'Your invite code')
            : `Joining ${escapeHtml(view.session.hostName)}`;
        const codeHint = isHost
            ? 'Show this code, or send it however you normally chat.'
            : 'Send this back to the host — they enter it to finish connecting.';
        const scanButton = scanSupported()
            ? `<button class="btn ghost" id="p2pScanBtn" style="width:100%;margin-top:8px;">Scan their code</button>`
            : '';
        const replyBox = isHost
            ? `<label class="lan-label" for="p2pReplyInput">Their reply code</label>
               <textarea id="p2pReplyInput" class="lan-input p2p-code" rows="3"
                   placeholder="MYTCG2.…" ${view.busy ? 'disabled' : ''}></textarea>
               ${scanButton}
               <button class="btn" id="p2pConnectBtn" style="width:100%;margin-top:8px;"
                   ${view.busy ? 'disabled' : ''}>Connect</button>`
            : '<p class="tiny" style="margin-top:12px;">Waiting for the host to connect…</p>';
        ui.lanBody.innerHTML = `
            <div class="lan-section-title">${title}</div>
            <p class="tiny" style="margin:0 0 8px;">${codeHint}</p>
            <div class="p2p-qr">${qrCodeHtml(view.code)}</div>
            <textarea id="p2pCodeOut" class="lan-input p2p-code" rows="3" readonly>${escapeHtml(view.code)}</textarea>
            <button class="btn ghost" id="p2pCopyBtn" style="width:100%;margin-top:8px;">Copy code</button>
            ${replyBox}
            <p class="tiny p2p-status">${escapeHtml(view.status || '')}</p>
            <button class="btn ghost" id="p2pCancelBtn" style="width:100%;margin-top:8px;">Cancel</button>`;
        document.getElementById('p2pCopyBtn').addEventListener('click', () => copyToClipboard(view.code));
        document.getElementById('p2pCancelBtn').addEventListener('click', cancelP2p);
        const scanBtn = document.getElementById('p2pScanBtn');
        if (scanBtn) {
            scanBtn.addEventListener('click', () => scanQrCode((code) => connectP2pHost(code)));
        }
        const connectBtn = document.getElementById('p2pConnectBtn');
        if (connectBtn) {
            connectBtn.addEventListener('click', () => {
                const code = document.getElementById('p2pReplyInput').value;
                if (!code.trim()) { showToast('Paste the reply code your friend sent back.'); return; }
                connectP2pHost(code);
            });
        }
    }

    // A code is ~170 characters, which is a miserable thing to read off one
    // screen and type into another but a comfortable QR symbol. Rendering it
    // never blocks showing the code itself: if it somehow will not encode, the
    // text is still there to copy.
    function qrCodeHtml(code) {
        try {
            return qrSvg(code, { moduleSize: 4 });
        } catch (error) {
            return '';
        }
    }

    function lanPeerRowsHtml() {
        const openPeers = lanPeers.filter((p) => p.lobby);
        return openPeers.length
            ? openPeers.map((p, i) => `
                <div class="lan-peer" data-peer-idx="${i}">
                    <div><div class="lan-peer-name">${escapeHtml(p.name)}</div>
                        <div class="tiny">${escapeHtml(p.lobby.host_name)}'s game · ${p.lobby.joined} ${p.lobby.joined === 1 ? 'player' : 'players'} in</div></div>
                    <button class="btn small" data-join-idx="${i}">Join</button>
                </div>`).join('')
            : '<p class="tiny">No open games found yet. Make sure everyone is on the same Wi‑Fi.</p>';
    }

    // Refresh only the peer list. Peer polling must not rebuild the whole panel,
    // or it would tear down the focused name input and dismiss the keyboard.
    function renderLanPeers() {
        const container = document.getElementById('lanPeers');
        if (!container) return;
        container.innerHTML = lanPeerRowsHtml();
        const openPeers = lanPeers.filter((p) => p.lobby);
        openPeers.forEach((p, i) => {
            const btn = container.querySelector(`[data-join-idx="${i}"]`);
            if (btn) btn.addEventListener('click', () => joinLan(p));
        });
    }

    async function leaveLanLobby() {
        // An invite-code lobby only exists as long as the direct connection
        // does, so leaving it drops the connection too.
        closeActiveP2p();
        setP2pTransport(null);
        p2pHub = null;
        lanLobby = null;
        startPeerPolling();
        renderLan();
    }

    // --- LAN trading ---------------------------------------------------------
    // A trade session in progress: { trade_id, host_base, match_id, my_pid,
    // opp_pid, offers, confirmed, status }. tradeMine is my staged offer (card
    // ids), tradeTimer polls the host for the other side.
    let trade = null;
    let tradeMine = [];
    let tradeTimer = null;

    async function openTrade() {
        const ctx = game.lanContext();
        if (!ctx || !ctx.matchId) return;
        await ensureCollection();
        const opp = (ctx.players || []).map(Number).find((pid) => pid !== Number(ctx.playerId));
        if (opp == null) { showToast('No opponent to trade with.'); return; }
        try {
            const data = await lanPost(ctx.hostBase, '/api/lan/trade/propose', {
                match_id: ctx.matchId, a_pid: Number(ctx.playerId), b_pid: Number(opp),
            });
            trade = {
                ...data.trade, host_base: ctx.hostBase,
                my_pid: Number(ctx.playerId), opp_pid: Number(opp),
            };
            // Resume any offer already recorded for me (e.g. reopening the sheet).
            tradeMine = (data.trade.offers[String(ctx.playerId)] || []).slice();
        } catch (error) {
            showToast(`Could not open trade: ${error}`);
            return;
        }
        ui.tradeModal.classList.add('open');
        ui.tradeModal.setAttribute('aria-hidden', 'false');
        renderTrade();
        startTradePolling();
    }

    function closeTrade({ silentCancel = false } = {}) {
        stopTradePolling();
        if (trade && trade.status === 'open' && !silentCancel) {
            lanPost(trade.host_base, '/api/lan/trade/cancel', { trade_id: trade.trade_id }).catch(() => {});
        }
        trade = null;
        tradeMine = [];
        ui.tradeModal.classList.remove('open');
        ui.tradeModal.setAttribute('aria-hidden', 'true');
    }

    function tradeSignature() {
        return trade ? JSON.stringify([trade.offers, trade.confirmed, trade.status]) : '';
    }

    function startTradePolling() {
        stopTradePolling();
        const tick = async () => {
            if (!trade) return;
            try {
                const data = await lanPost(trade.host_base, '/api/lan/trade/state', { trade_id: trade.trade_id });
                if (data.ok && data.trade) {
                    const before = tradeSignature();
                    Object.assign(trade, data.trade);
                    if (trade.status === 'completed') { finishTrade(); return; }
                    if (trade.status === 'cancelled') { showToast('Trade cancelled.'); closeTrade({ silentCancel: true }); return; }
                    // Only re-render when the other side actually changed something,
                    // so a poll never detaches the card the user is about to tap.
                    if (tradeSignature() !== before) renderTrade();
                }
            } catch (error) { /* transient */ }
            tradeTimer = setTimeout(tick, 1200);
        };
        tradeTimer = setTimeout(tick, 1200);
    }
    function stopTradePolling() {
        if (tradeTimer) { clearTimeout(tradeTimer); tradeTimer = null; }
    }

    async function pushMyOffer() {
        try {
            const data = await lanPost(trade.host_base, '/api/lan/trade/offer', {
                trade_id: trade.trade_id, player_id: trade.my_pid, card_ids: tradeMine,
            });
            if (data.ok) Object.assign(trade, data.trade);
        } catch (error) { showToast(`${error}`); }
        renderTrade();
    }

    async function confirmTrade() {
        try {
            const data = await lanPost(trade.host_base, '/api/lan/trade/confirm', {
                trade_id: trade.trade_id, player_id: trade.my_pid,
            });
            if (data.ok) {
                Object.assign(trade, data.trade);
                if (trade.status === 'completed') { finishTrade(); return; }
            }
        } catch (error) { showToast(`${error}`); }
        renderTrade();
    }

    function finishTrade() {
        const gained = (trade.offers[String(trade.opp_pid)] || []).slice();
        const lost = (trade.offers[String(trade.my_pid)] || []).slice();
        applyTradeResult({ gained, lost });
        const summary = gained.length || lost.length
            ? `Trade complete — gave ${lost.length}, received ${gained.length}.`
            : 'Trade complete.';
        showToast(summary);
        closeTrade({ silentCancel: true });
    }

    function cardLabel(cardId) {
        const card = cardById(cardId);
        return card ? card.name : cardId;
    }

    function renderTrade() {
        if (!trade || !ui.tradeBody) return;
        const theirs = trade.offers[String(trade.opp_pid)] || [];
        const iConfirmed = Boolean(trade.confirmed[String(trade.my_pid)]);
        const theyConfirmed = Boolean(trade.confirmed[String(trade.opp_pid)]);

        const mineChips = tradeMine.length
            ? tradeMine.map((id) => `<span class="trade-chip removable" data-remove="${escapeHtml(id)}">${escapeHtml(cardLabel(id))}</span>`).join('')
            : '<span class="tiny">Add cards below…</span>';
        const theirChips = theirs.length
            ? theirs.map((id) => `<span class="trade-chip">${escapeHtml(cardLabel(id))}</span>`).join('')
            : '<span class="tiny">Nothing yet…</span>';

        // Cards you can still offer: owned (>=1) and not already staged.
        const staged = new Set(tradeMine);
        const pickable = allCollectionCards()
            .filter((c) => ownedCount(c.id) >= 1 && !staged.has(c.id))
            // De-dupe cards shared across decks.
            .filter((c, i, arr) => arr.findIndex((x) => x.id === c.id) === i);
        const pickerChips = pickable.map((c) =>
            `<span class="trade-chip" data-add="${escapeHtml(c.id)}">${escapeHtml(c.name)}</span>`).join('');

        const statusText = iConfirmed && theyConfirmed ? 'Both confirmed!'
            : iConfirmed ? 'You confirmed. Waiting for them…'
            : theyConfirmed ? 'They confirmed. Your move.'
            : 'Add cards, then confirm.';

        ui.tradeBody.innerHTML = `
            <div class="trade-cols">
                <div><div class="trade-col-title">You give ${iConfirmed ? '<span class="trade-confirmed">✓</span>' : ''}</div>
                    <div class="trade-offer" id="tradeMine">${mineChips}</div></div>
                <div><div class="trade-col-title">They give ${theyConfirmed ? '<span class="trade-confirmed">✓</span>' : ''}</div>
                    <div class="trade-offer">${theirChips}</div></div>
            </div>
            <div class="trade-status ${iConfirmed && theyConfirmed ? 'ready' : ''}">${statusText}</div>
            <div class="trade-picker">
                <div class="trade-col-title">Your cards</div>
                <div class="trade-offer" style="max-height:180px;overflow:auto;">${pickerChips || '<span class="tiny">No cards left to trade.</span>'}</div>
            </div>
            <div class="trade-actions">
                <button class="btn ghost" id="tradeCancelBtn">Cancel</button>
                <button class="btn" id="tradeConfirmBtn" ${iConfirmed ? 'disabled' : ''}>${iConfirmed ? 'Confirmed' : 'Confirm trade'}</button>
            </div>`;

        ui.tradeBody.querySelectorAll('[data-add]').forEach((el) => {
            el.addEventListener('click', () => { tradeMine.push(el.dataset.add); pushMyOffer(); });
        });
        ui.tradeBody.querySelectorAll('[data-remove]').forEach((el) => {
            el.addEventListener('click', () => {
                tradeMine = tradeMine.filter((id) => id !== el.dataset.remove);
                pushMyOffer();
            });
        });
        document.getElementById('tradeCancelBtn').addEventListener('click', () => closeTrade());
        document.getElementById('tradeConfirmBtn').addEventListener('click', confirmTrade);
    }

    // --- Card grouping (companion stacks) ------------------------------------

    // Groups a card list into entries: single cards, or a companion stack when
    // both partners are present in the list. Order is preserved.
    function groupCards(cards) {
        const entries = [];
        const consumed = new Set();
        for (const card of cards) {
            if (consumed.has(card.id)) continue;
            const partnerName = companionPartnerName(card.name);
            const partner = partnerName ? cards.find((c) => c.name === partnerName && !consumed.has(c.id)) : null;
            if (partner) {
                consumed.add(card.id);
                consumed.add(partner.id);
                entries.push({ kind: 'stack', cards: [card, partner] });
            } else {
                consumed.add(card.id);
                entries.push({ kind: 'card', cards: [card] });
            }
        }
        return entries;
    }

    // Win rate of a card inside the deck being edited (never global).
    function deckWrBadgeHtml(card) {
        if (!editorDeckId) return '';
        const s = cardDeckStats(editorDeckId, card.id);
        if (!s) return '';
        return `<div class="mini-wr">${Math.round(s.rate * 100)}% WR</div>`;
    }

    function miniCardHtml(card, { inDeck = false, badgeHtml = '' } = {}) {
        if (!card) return '';
        return `
            <div class="mini-card ${inDeck ? 'in-deck' : ''}" data-card-id="${escapeHtml(card.id)}">
                <div class="mini-head">
                    <span class="stat-badge cost">${card.cost ?? '?'}</span>
                    <span class="stat-badge power">${card.power !== null && card.power !== undefined ? card.power : '?'}</span>
                </div>
                <div class="mini-media">${cardArtTag(card.name, 'mini-art')}</div>
                <div class="mini-name">${escapeHtml(card.name || '')}</div>
                ${badgeHtml}
            </div>
        `;
    }

    // cardBadge: per-card badge html (deck win rate, match score);
    // action: quick add/remove corner button.
    function entryHtml(entry, index, { inDeck = false, cardBadge = null, action = null } = {}) {
        const actionHtml = action
            ? `<button class="mini-act ${action}" data-entry-act="${action}"
                aria-label="${action === 'add' ? 'Add to deck' : 'Remove from deck'}">${action === 'add' ? '+' : '−'}</button>`
            : '';
        const badgeOf = (card) => (cardBadge ? cardBadge(card) : '');
        if (entry.kind === 'stack') {
            return `
                <div class="mini-stack ${inDeck ? 'in-deck' : ''}" data-entry-index="${index}">
                    ${entry.cards.map((card) => miniCardHtml(card, { badgeHtml: badgeOf(card) })).join('')}
                    <div class="mini-stack-tag">Companions</div>
                    ${actionHtml}
                </div>
            `;
        }
        return `
            <div class="mini-entry" data-entry-index="${index}">
                ${miniCardHtml(entry.cards[0], { inDeck, badgeHtml: badgeOf(entry.cards[0]) })}
                ${actionHtml}
            </div>
        `;
    }

    // --- Card popup (read / add / remove) --------------------------------------

    function entryTitle(entry) {
        return entry.cards.map((c) => c.name).join(' & ');
    }

    // Deck-scoped stats line for the popup: "60% win rate in this deck
    // (5 games) · 75% when played (4 plays)". Nothing outside a deck context.
    function cardStatsNote(card) {
        if (!editorDeckId) return null;
        const s = cardDeckStats(editorDeckId, card.id);
        if (!s) return null;
        const inDeckPart = `${Math.round(s.rate * 100)}% win rate in this deck (${s.games} game${s.games === 1 ? '' : 's'})`;
        const playedPart = s.played
            ? `${Math.round(s.playedRate * 100)}% when played (${s.played} play${s.played === 1 ? '' : 's'})`
            : 'not played yet';
        return `${inDeckPart} · ${playedPart}.`;
    }

    function entryNote(entry) {
        const parts = [];
        if (entry.kind === 'stack') parts.push('Companions — they join and leave a deck together.');
        for (const card of entry.cards) {
            const note = cardStatsNote(card);
            if (note) parts.push(entry.cards.length > 1 ? `${card.name} — ${note}` : note);
        }
        return parts.join(' ');
    }

    // Every collection/deck card opens in the shared card-stack popup so it
    // can actually be read; deck building actions ride along as buttons.
    function openCardPopup(entry, { action = null, actionLabel = '', onAction = null } = {}) {
        cardStack.open({
            mode: 'view',
            expandAll: true,
            title: entryTitle(entry),
            cards: entry.cards.map((card) => ({ card, option: card.id })),
            note: entryNote(entry),
            extras: action ? [{ value: action, label: actionLabel, kind: action }] : [],
            onExtra: () => {
                if (onAction) onAction();
            },
        });
    }

    // --- Decks screen -----------------------------------------------------------

    function currentEditorIds() {
        return deckCardIds(editorDeckId, stockIds(editorDeckId));
    }

    function deckStatsLine(deckId) {
        const wr = deckWinRate(deckId);
        if (!wr) return '<span class="deck-tile-stats muted">No games yet</span>';
        return `<span class="deck-tile-stats">${Math.round(wr.rate * 100)}% WR · ${wr.games} game${wr.games === 1 ? '' : 's'}</span>`;
    }

    function renderDeckList() {
        const selectedId = getSelectedDeckId();
        ui.decksTitle.textContent = 'Decks';
        ui.decksTop.innerHTML = `
            <div class="deck-row">
                ${allDeckIds().map((deckId) => {
                    const selected = deckId === selectedId;
                    const size = deckCardIds(deckId, stockIds(deckId)).length;
                    const sizeWarn = size !== DECK_SIZE ? `<span class="deck-tag deck-tag-warn">${size}/${DECK_SIZE}</span>` : '';
                    return `
                        <div class="deck-tile ${selected ? 'selected' : ''}" data-deck-id="${deckId}">
                            <div class="deck-tile-art">${deckArtHtml(deckId)}</div>
                            <div class="deck-tile-name">${escapeHtml(deckDisplayName(deckId))}</div>
                            ${deckStatsLine(deckId)}
                            <div class="deck-tile-tags">
                                ${isCustomDeck(deckId) ? '<span class="deck-tag">Custom</span>' : ''}
                                ${deckIsEdited(deckId) ? '<span class="deck-tag">Edited</span>' : ''}
                                ${sizeWarn}
                            </div>
                            <div class="deck-tile-actions">
                                ${selected
                                    ? '<button class="btn deck-action-btn deck-selected-btn" disabled>Selected</button>'
                                    : '<button class="btn deck-action-btn" data-deck-action="select">Select</button>'}
                                <button class="btn deck-action-btn ghost" data-deck-action="edit">Edit</button>
                            </div>
                        </div>
                    `;
                }).join('')}
                <button class="deck-tile deck-tile-new" id="deckNewBtn">
                    <div class="deck-new-plus">+</div>
                    <div class="deck-tile-name">New deck</div>
                </button>
            </div>
        `;

        ui.decksTop.querySelectorAll('.deck-tile[data-deck-id]').forEach((tile) => {
            const deckId = tile.dataset.deckId;
            tile.querySelectorAll('[data-deck-action]').forEach((btn) => {
                btn.addEventListener('click', (event) => {
                    event.stopPropagation();
                    if (btn.dataset.deckAction === 'select') {
                        selectDeck(deckId);
                        renderDecks();
                        renderMenu();
                    } else {
                        openEditor(deckId);
                    }
                });
            });
            // Tapping the tile body also opens the editor (like tapping a
            // shop item's preview).
            tile.addEventListener('click', () => openEditor(deckId));
        });
        const newBtn = ui.decksTop.querySelector('#deckNewBtn');
        if (newBtn) {
            newBtn.addEventListener('click', () => {
                openEditor(createCustomDeck());
            });
        }
    }

    function openEditor(deckId) {
        pushNav({ screen: 'decks', editor: deckId });
        editorDeckId = deckId;
        confirmingDelete = false;
        styleOpen = false;
        renderDecks();
    }

    // --- Per-deck cosmetics (deck editor section) --------------------------------

    function cosmeticRowHtml(kind, label, items) {
        const current = deckCosmetic(editorDeckId, kind); // null = default
        const ownedItems = items.filter((item) => ownsItem(kind, item.id));
        const defaultItem = items.find((item) => item.id === equippedItem(kind));
        const previewHtml = (item) => (kind === 'cardBack'
            ? `<span class="cos-mini cos-mini-back" data-cardback="${item.id}"></span>`
            : `<span class="cos-mini cos-mini-board" data-board="${item.id}"></span>`);
        return `
            <div class="cos-row">
                <span class="cos-row-label">${label}</span>
                <div class="cos-chips">
                    <button class="cos-chip ${current === null ? 'active' : ''}" data-cos-kind="${kind}" data-cos-id=""
                        title="Use your default (${escapeHtml(defaultItem ? defaultItem.name : 'Classic')})">Default</button>
                    ${ownedItems.map((item) => `
                        <button class="cos-chip cos-chip-preview ${current === item.id ? 'active' : ''}"
                            data-cos-kind="${kind}" data-cos-id="${item.id}" title="${escapeHtml(item.name)}">
                            ${previewHtml(item)}
                        </button>
                    `).join('')}
                </div>
            </div>
        `;
    }

    function emoteRowHtml() {
        const owned = ownedEmotes();
        const loadout = deckEmoteLoadout(editorDeckId);
        const active = new Set((loadout || equippedEmoteIds()).slice(0, MAX_ACTIVE_EMOTES));
        return `
            <div class="cos-row">
                <span class="cos-row-label">Emotes</span>
                <div class="cos-chips">
                    ${owned.map((emote) => `
                        <button class="cos-chip cos-emote-chip ${active.has(emote.id) ? 'active' : ''}"
                            data-emote-id="${emote.id}">${escapeHtml(emote.text)}</button>
                    `).join('')}
                </div>
            </div>
        `;
    }

    function bindCosmeticRows() {
        ui.decksTop.querySelectorAll('[data-cos-kind]').forEach((btn) => {
            btn.addEventListener('click', () => {
                setDeckCosmetic(editorDeckId, btn.dataset.cosKind, btn.dataset.cosId || null);
                renderDecks();
            });
        });
        ui.decksTop.querySelectorAll('[data-emote-id]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const current = new Set((deckEmoteLoadout(editorDeckId) || equippedEmoteIds()).slice(0, MAX_ACTIVE_EMOTES));
                const emoteId = btn.dataset.emoteId;
                if (current.has(emoteId)) {
                    if (current.size === 1) {
                        showToast('Keep at least one emote in the loadout.');
                        return;
                    }
                    current.delete(emoteId);
                } else {
                    if (current.size >= MAX_ACTIVE_EMOTES) {
                        showToast(`Pick at most ${MAX_ACTIVE_EMOTES} emotes — remove one first.`);
                        return;
                    }
                    current.add(emoteId);
                }
                // A loadout matching the shop-equipped default is no override.
                const equipped = equippedEmoteIds();
                const next = current.size === equipped.length && equipped.every((id) => current.has(id))
                    ? null
                    : Array.from(current);
                setDeckEmoteLoadout(editorDeckId, next);
                renderDecks();
            });
        });
    }

    // --- Recommendations ---------------------------------------------------------

    // Embedding-based recommendations: candidates outside the deck ranked by
    // cosine similarity to the deck's centroid vector (see embedding.js).
    function recommendedEntries() {
        if (!recommender || !stockCardsByDeck) return [];
        const ids = new Set(currentEditorIds());
        const deckCards = currentEditorIds().map(cardById).filter(Boolean);
        if (!deckCards.length) return [];
        const candidates = allCollectionCards().filter((card) => !ids.has(card.id));
        const recs = recommender(deckCards, candidates, 12);
        const scoreById = new Map(recs.map((r) => [r.card.id, r.score]));
        // Companion halves surface as one stack; keep relevance order.
        const entries = [];
        const seen = new Set();
        for (const { card, score } of recs) {
            if (seen.has(card.id)) continue;
            const partnerName = companionPartnerName(card.name);
            const partner = partnerName ? cardByName(partnerName) : null;
            if (partner && !ids.has(partner.id)) {
                seen.add(card.id);
                seen.add(partner.id);
                entries.push({ kind: 'stack', cards: [card, partner], score: Math.max(score, scoreById.get(partner.id) || 0) });
            } else if (!partner || ids.has(partner.id)) {
                seen.add(card.id);
                entries.push({ kind: 'card', cards: [card], score });
            }
            if (entries.length >= 6) break;
        }
        return entries;
    }

    let lastRecommendedEntries = [];

    // Cosine similarity displayed relative to the best candidate: the top
    // recommendation reads 100%, the rest scale down from there.
    function matchBadgeHtml(entry, topScore) {
        if (!topScore) return '';
        const pct = Math.max(1, Math.round((entry.score / topScore) * 100));
        return `<div class="mini-match">${pct}% match</div>`;
    }

    function recommendedRowHtml() {
        lastRecommendedEntries = recommendedEntries();
        const topScore = lastRecommendedEntries.length ? lastRecommendedEntries[0].score : 0;
        const body = lastRecommendedEntries.length
            ? lastRecommendedEntries.map((entry, i) => entryHtml(entry, i, {
                cardBadge: () => matchBadgeHtml(entry, topScore),
                action: 'add',
            })).join('')
            : `<div class="rec-empty tiny">${currentEditorIds().length
                ? 'No recommendations right now.'
                : 'Add a few cards and recommendations will appear here.'}</div>`;
        return `
            <details class="rec-wrap cos-section" id="recSection" ${recOpen ? 'open' : ''}>
                <summary class="collection-label cos-summary">Recommended for this deck</summary>
                <div class="rec-row" id="recRow">${body}</div>
            </details>
        `;
    }

    // --- Deck editor -----------------------------------------------------------

    function addEntryToDeck(entry) {
        const ids = currentEditorIds();
        const newIds = entry.cards.map((c) => c.id).filter((id) => !ids.includes(id));
        if (!newIds.length) return;
        if (ids.length + newIds.length > DECK_SIZE) {
            showToast(`A deck holds ${DECK_SIZE} cards — remove ${ids.length + newIds.length - DECK_SIZE} first.`);
            return;
        }
        setDeckCards(editorDeckId, [...ids, ...newIds], stockIds(editorDeckId));
        renderDecks();
    }

    function removeEntryFromDeck(entry) {
        const removeIds = new Set(entry.cards.map((c) => c.id));
        const ids = currentEditorIds().filter((id) => !removeIds.has(id));
        setDeckCards(editorDeckId, ids, stockIds(editorDeckId));
        renderDecks();
    }

    // The +/- corner buttons: act directly, without opening the card popup.
    function bindQuickActions(container, entriesFor) {
        container.querySelectorAll('.mini-act').forEach((btn) => {
            btn.addEventListener('click', (event) => {
                event.stopPropagation();
                const el = btn.closest('[data-entry-index]');
                const entry = el && entriesFor()[Number(el.dataset.entryIndex)];
                if (!entry) return;
                if (btn.dataset.entryAct === 'add') addEntryToDeck(entry);
                else removeEntryFromDeck(entry);
            });
        });
    }

    function renderDeckEditor() {
        const deckId = editorDeckId;
        const ids = currentEditorIds();
        const custom = isCustomDeck(deckId);
        const deckEntries = groupCards(ids.map(cardById).filter(Boolean));
        ui.decksTitle.textContent = 'Edit deck';
        ui.decksTop.innerHTML = `
            <div class="deck-editor">
                <div class="deck-editor-head">
                    <input class="deck-name-input" id="deckNameInput" maxlength="30"
                        value="${escapeHtml(deckDisplayName(deckId))}" aria-label="Deck name">
                    <span class="deck-count ${ids.length === DECK_SIZE ? 'ok' : 'warn'}">${ids.length}/${DECK_SIZE}</span>
                    ${custom
                        ? `<button class="btn deck-reset-btn danger-ghost" id="deckDeleteBtn">${confirmingDelete ? 'Really delete?' : 'Delete'}</button>`
                        : '<button class="btn deck-reset-btn" id="deckResetBtn" title="Restore the stock deck list and name">Reset</button>'}
                </div>
                <div class="deck-grid">
                    ${deckEntries.length
                        ? deckEntries.map((entry, i) => entryHtml(entry, i, { cardBadge: deckWrBadgeHtml, action: 'remove' })).join('')
                        : Array.from({ length: DECK_SIZE }, () => '<div class="deck-slot-empty" aria-hidden="true"></div>').join('')}
                </div>
                <details class="cos-section" id="deckStyleSection" ${styleOpen ? 'open' : ''}>
                    <summary class="collection-label cos-summary">Deck style</summary>
                    <div class="cos-body">
                        ${cosmeticRowHtml('cardBack', 'Card back', CARD_BACKS)}
                        ${cosmeticRowHtml('board', 'Board', BOARDS)}
                        ${emoteRowHtml()}
                    </div>
                </details>
                ${recommendedRowHtml()}
            </div>
        `;

        const nameInput = ui.decksTop.querySelector('#deckNameInput');
        nameInput.addEventListener('change', () => {
            renameDeck(deckId, nameInput.value);
            nameInput.value = deckDisplayName(deckId);
            renderMenu();
        });
        const resetBtn = ui.decksTop.querySelector('#deckResetBtn');
        if (resetBtn) {
            resetBtn.addEventListener('click', () => {
                resetDeck(deckId);
                renderDecks();
                renderMenu();
            });
        }
        const deleteBtn = ui.decksTop.querySelector('#deckDeleteBtn');
        if (deleteBtn) {
            deleteBtn.addEventListener('click', () => {
                if (!confirmingDelete) {
                    confirmingDelete = true;
                    deleteBtn.textContent = 'Really delete?';
                    setTimeout(() => {
                        confirmingDelete = false;
                        if (deleteBtn.isConnected) deleteBtn.textContent = 'Delete';
                    }, 2600);
                    return;
                }
                deleteCustomDeck(deckId);
                editorDeckId = null;
                renderDecks();
                renderMenu();
            });
        }

        const styleSection = ui.decksTop.querySelector('#deckStyleSection');
        if (styleSection) {
            styleSection.addEventListener('toggle', () => {
                styleOpen = styleSection.open;
            });
        }
        const recSection = ui.decksTop.querySelector('#recSection');
        if (recSection) {
            recSection.addEventListener('toggle', () => {
                recOpen = recSection.open;
            });
        }

        // Deck cards: popup with a Remove action.
        const deckGrid = ui.decksTop.querySelector('.deck-grid');
        deckGrid.querySelectorAll('[data-entry-index]').forEach((el) => {
            el.addEventListener('click', () => {
                const entry = deckEntries[Number(el.dataset.entryIndex)];
                if (!entry) return;
                openCardPopup(entry, {
                    action: 'remove',
                    actionLabel: entry.kind === 'stack' ? 'Remove both from deck' : 'Remove from deck',
                    onAction: () => removeEntryFromDeck(entry),
                });
            });
        });
        bindQuickActions(deckGrid, () => deckEntries);

        // Recommended cards: popup with an Add action.
        const recRow = ui.decksTop.querySelector('.rec-row');
        recRow.querySelectorAll('[data-entry-index]').forEach((el) => {
            el.addEventListener('click', () => {
                const entry = lastRecommendedEntries[Number(el.dataset.entryIndex)];
                if (!entry) return;
                openCardPopup(entry, {
                    action: 'add',
                    actionLabel: entry.kind === 'stack' ? 'Add both to deck' : 'Add to deck',
                    onAction: () => addEntryToDeck(entry),
                });
            });
        });
        bindQuickActions(recRow, () => lastRecommendedEntries);

        bindCosmeticRows();
    }

    // --- Collection browser -------------------------------------------------------

    function inNumericRange(value, range) {
        const n = Number(value);
        if (!Number.isFinite(n)) return false;
        const [min, max] = range;
        return n >= min && (max === null || n <= max);
    }

    const COST_FILTERS = {
        low: { label: 'Cost 0–1', range: [0, 1] },
        mid: { label: 'Cost 2–3', range: [2, 3] },
        high: { label: 'Cost 4+', range: [4, null] },
    };

    const POWER_FILTERS = {
        low: { label: 'Power 0–2', range: [0, 2] },
        mid: { label: 'Power 3–5', range: [3, 5] },
        high: { label: 'Power 6+', range: [6, null] },
    };

    const SORTERS = {
        name: (a, b) => String(a.name).localeCompare(String(b.name)),
        'cost-asc': (a, b) => (Number(a.cost) || 0) - (Number(b.cost) || 0),
        'cost-desc': (a, b) => (Number(b.cost) || 0) - (Number(a.cost) || 0),
        'power-asc': (a, b) => (Number(a.power) || 0) - (Number(b.power) || 0),
        'power-desc': (a, b) => (Number(b.power) || 0) - (Number(a.power) || 0),
    };

    function visibleCollectionCards() {
        let cards = allCollectionCards();
        if (typeFilter) cards = cards.filter((c) => String(c.type || '') === typeFilter);
        if (costFilter) cards = cards.filter((c) => inNumericRange(c.cost, COST_FILTERS[costFilter].range));
        if (powerFilter) cards = cards.filter((c) => inNumericRange(c.power, POWER_FILTERS[powerFilter].range));

        if (searchQuery.trim() && cardSearch) {
            // Embedding search: relevance order, typo tolerant.
            const results = cardSearch(searchQuery) || [];
            const rank = new Map(results.map((r, i) => [r.card.id, i]));
            cards = cards
                .filter((c) => rank.has(c.id))
                .sort((a, b) => rank.get(a.id) - rank.get(b.id));
        } else if (SORTERS[sortMode]) {
            cards = cards.slice().sort(SORTERS[sortMode]);
        }
        return cards;
    }

    function renderCollectionControls() {
        if (!ui.collectionControls) return;
        if (!stockCardsByDeck) return; // type options need the collection
        if (controlsBuilt) return;
        controlsBuilt = true;

        const types = Array.from(new Set(allCollectionCards().map((c) => String(c.type || '')).filter(Boolean))).sort();
        ui.collectionControls.innerHTML = `
            <input type="search" class="collection-search" id="collectionSearch"
                placeholder="Search cards…" autocomplete="off" spellcheck="false">
            <div class="collection-filters">
                <select id="collectionSort" aria-label="Sort cards">
                    <option value="name">Name A–Z</option>
                    <option value="cost-asc">Cost: low first</option>
                    <option value="cost-desc">Cost: high first</option>
                    <option value="power-asc">Power: low first</option>
                    <option value="power-desc">Power: high first</option>
                </select>
                <select id="collectionType" aria-label="Filter by type">
                    <option value="">All types</option>
                    ${types.map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join('')}
                </select>
                <select id="collectionCost" aria-label="Filter by cost">
                    <option value="">Any cost</option>
                    ${Object.entries(COST_FILTERS).map(([key, f]) => `<option value="${key}">${f.label}</option>`).join('')}
                </select>
                <select id="collectionPower" aria-label="Filter by power">
                    <option value="">Any power</option>
                    ${Object.entries(POWER_FILTERS).map(([key, f]) => `<option value="${key}">${f.label}</option>`).join('')}
                </select>
            </div>
        `;

        ui.collectionControls.querySelector('#collectionSearch').addEventListener('input', (event) => {
            searchQuery = event.target.value;
            renderCollectionGrid();
        });
        const bindSelect = (id, apply) => {
            ui.collectionControls.querySelector(id).addEventListener('change', (event) => {
                apply(event.target.value);
                renderCollectionGrid();
            });
        };
        bindSelect('#collectionSort', (v) => { sortMode = v; });
        bindSelect('#collectionType', (v) => { typeFilter = v; });
        bindSelect('#collectionCost', (v) => { costFilter = v; });
        bindSelect('#collectionPower', (v) => { powerFilter = v; });
    }

    let lastCollectionEntries = [];

    function renderCollectionGrid() {
        if (collectionError) {
            ui.collectionGrid.innerHTML = `<div class="tiny">Collection unavailable: ${escapeHtml(collectionError)}</div>`;
            return;
        }
        const cards = visibleCollectionCards();
        if (!allCollectionCards().length) {
            ui.collectionGrid.innerHTML = '<div class="tiny">Loading collection…</div>';
            return;
        }
        ui.collectionLabel.textContent = 'Your collection';
        if (!cards.length) {
            ui.collectionGrid.innerHTML = '<div class="tiny">No cards match your search or filters.</div>';
            return;
        }

        const deckIds = editorDeckId ? new Set(currentEditorIds()) : new Set();
        lastCollectionEntries = groupCards(cards);
        ui.collectionGrid.innerHTML = lastCollectionEntries
            .map((entry, i) => {
                const inDeck = entry.cards.every((c) => deckIds.has(c.id));
                return entryHtml(entry, i, {
                    inDeck,
                    action: editorDeckId ? (inDeck ? 'remove' : 'add') : null,
                });
            })
            .join('');

        ui.collectionGrid.querySelectorAll('[data-entry-index]').forEach((el) => {
            el.addEventListener('click', () => {
                const entry = lastCollectionEntries[Number(el.dataset.entryIndex)];
                if (!entry) return;
                if (!editorDeckId) {
                    openCardPopup(entry); // browse mode: just read the card
                    return;
                }
                const allInDeck = entry.cards.every((c) => deckIds.has(c.id));
                if (allInDeck) {
                    openCardPopup(entry, {
                        action: 'remove',
                        actionLabel: entry.kind === 'stack' ? 'Remove both from deck' : 'Remove from deck',
                        onAction: () => removeEntryFromDeck(entry),
                    });
                    return;
                }
                openCardPopup(entry, {
                    action: 'add',
                    actionLabel: entry.kind === 'stack' ? 'Add both to deck' : 'Add to deck',
                    onAction: () => addEntryToDeck(entry),
                });
            });
        });
        if (editorDeckId) bindQuickActions(ui.collectionGrid, () => lastCollectionEntries);
    }

    function renderDecks() {
        if (editorDeckId) {
            renderDeckEditor();
        } else {
            renderDeckList();
        }
        renderCollectionControls();
        renderCollectionGrid();
    }

    async function openDecks() {
        if (!decksUnlocked()) {
            showToast('Play your first game to unlock Decks.');
            return;
        }
        editorDeckId = null;
        pushNav({ screen: 'decks' });
        showScreen('decks');
        renderDecks();
        await ensureCollection();
        renderDecks();
    }

    // --- Shop screen ---------------------------------------------------------

    function shopPreviewHtml(kind, item) {
        // Tapping the tile shows that back full size, bought or not (js/cardback.js).
        if (kind === 'cardBack') {
            return `<div class="cardback-preview" data-cardback="${item.id}" data-cardback-view="${item.id}"
                role="button" tabindex="0" aria-label="View ${escapeHtml(item.name)} full size"></div>`;
        }
        if (kind === 'board') return `<div class="board-preview" data-board="${item.id}"></div>`;
        return `<div class="emote-preview">${escapeHtml(item.text)}</div>`;
    }

    function shopButtonHtml(kind, item) {
        const owned = ownsItem(kind, item.id);
        if (!owned) {
            const affordable = getCrowns() >= item.cost;
            return `<button class="btn shop-item-btn" data-shop-action="buy" ${affordable ? '' : 'disabled'}>
                Buy · ${item.cost} <span class="crown-icon"></span></button>`;
        }
        if (kind === 'emote') {
            // Emotes equip into a loadout of MAX_ACTIVE_EMOTES; tapping an
            // equipped one takes it out again.
            return equippedEmoteIds().includes(item.id)
                ? '<button class="btn shop-item-btn equipped" data-shop-action="equip">Equipped</button>'
                : '<button class="btn shop-item-btn" data-shop-action="equip">Equip</button>';
        }
        const equipped = equippedItem(kind) === item.id;
        return equipped
            ? '<button class="btn shop-item-btn equipped" disabled>Equipped</button>'
            : '<button class="btn shop-item-btn" data-shop-action="equip">Equip</button>';
    }

    function renderShop() {
        updateCrowns();
        ui.shopTabs.innerHTML = SHOP_TABS.map((tab) => `
            <button class="shop-tab ${tab.kind === shopTab ? 'active' : ''}" data-shop-tab="${tab.kind}">${tab.label}</button>
        `).join('');
        ui.shopTabs.querySelectorAll('[data-shop-tab]').forEach((btn) => {
            btn.addEventListener('click', () => {
                shopTab = btn.dataset.shopTab;
                renderShop();
            });
        });

        const tab = SHOP_TABS.find((t) => t.kind === shopTab) || SHOP_TABS[0];
        ui.shopItems.innerHTML = tab.items.map((item) => `
            <div class="shop-item" data-item-id="${item.id}">
                ${shopPreviewHtml(tab.kind, item)}
                ${tab.kind === 'emote' ? '' : `<div class="shop-item-name">${escapeHtml(item.name)}</div>`}
                ${shopButtonHtml(tab.kind, item)}
            </div>
        `).join('');

        ui.shopItems.querySelectorAll('[data-shop-action]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const itemEl = btn.closest('.shop-item');
                const item = tab.items.find((i) => i.id === itemEl.dataset.itemId);
                if (!item) return;
                if (btn.dataset.shopAction === 'buy') {
                    if (buyItem(tab.kind, item.id, item.cost)) {
                        // Wear new cosmetics right away (emotes only when the
                        // loadout has a free slot).
                        if (tab.kind !== 'emote') equipItem(tab.kind, item.id);
                        else if (equippedEmoteIds().length < MAX_ACTIVE_EMOTES) toggleEquippedEmote(item.id);
                    }
                } else if (tab.kind === 'emote') {
                    const result = toggleEquippedEmote(item.id);
                    if (result === 'full') showToast(`Pick at most ${MAX_ACTIVE_EMOTES} emotes — unequip one first.`);
                    if (result === 'last') showToast('Keep at least one emote equipped.');
                } else {
                    equipItem(tab.kind, item.id);
                }
                renderShop();
            });
        });
    }

    function openShop() {
        if (!shopUnlocked()) {
            showToast('Earn your first crown to unlock the Shop.');
            return;
        }
        pushNav({ screen: 'shop' });
        showScreen('shop');
        renderShop();
    }

    // --- Replays -----------------------------------------------------------------

    function openReplays() {
        pushNav({ screen: 'replays' });
        replays.renderLibrary();
        showScreen('replays');
    }

    function openReplay(replayId) {
        if (!replays.load(replayId)) return;
        pushNav({ screen: 'replay', replayId });
        showScreen('replay');
    }

    // --- Wiring ------------------------------------------------------------------

    function init() {
        applyCosmetics();
        replays.init();
        if (ui.btnMenuReplays) {
            ui.btnMenuReplays.addEventListener('click', () => { openReplays(); });
        }
        if (ui.btnReplaysBack) ui.btnReplaysBack.addEventListener('click', () => { navBack(); });
        if (ui.btnReplayBack) ui.btnReplayBack.addEventListener('click', () => { navBack(); });
        ui.btnMenuPlay.addEventListener('click', () => {
            play();
        });
        ui.btnMenuDecks.addEventListener('click', () => {
            openDecks();
        });
        ui.btnMenuShop.addEventListener('click', () => {
            openShop();
        });
        if (ui.btnPassPlay) {
            ui.btnPassPlay.addEventListener('click', () => { openPassPlay(); });
        }
        if (ui.btnClosePassPlay) {
            ui.btnClosePassPlay.addEventListener('click', () => { closePassPlay(); });
        }
        if (ui.btnStartPassPlay) {
            ui.btnStartPassPlay.addEventListener('click', () => { startPassPlay(); });
        }
        if (ui.passPlayModal) {
            ui.passPlayModal.addEventListener('click', (event) => {
                if (event.target === ui.passPlayModal) closePassPlay();
            });
        }
        if (ui.btnLan) {
            ui.btnLan.addEventListener('click', () => { openLan(); });
        }
        if (ui.btnReconnect) {
            ui.btnReconnect.addEventListener('click', () => { reconnectSavedLan(); });
        }
        if (ui.btnCloseLan) {
            ui.btnCloseLan.addEventListener('click', () => { closeLan(); });
        }
        if (ui.lanModal) {
            ui.lanModal.addEventListener('click', (event) => {
                if (event.target === ui.lanModal) closeLan();
            });
        }
        if (ui.btnTrade) {
            ui.btnTrade.addEventListener('click', () => { openTrade(); });
        }
        if (ui.btnCloseTrade) {
            ui.btnCloseTrade.addEventListener('click', () => { closeTrade(); });
        }
        ui.btnDecksBack.addEventListener('click', () => {
            navBack();
        });
        ui.btnShopBack.addEventListener('click', () => {
            navBack();
        });
        // Hardware/browser back walks the in-app history (see handleNav).
        try {
            history.replaceState(navCurrent, '');
        } catch (error) { /* ignore */ }
        window.addEventListener('popstate', (event) => handleNav(event.state));
        renderMenu();
        // Quest countdowns tick while the menu is visible.
        setInterval(() => {
            if (ui.menuScreen && ui.menuScreen.classList.contains('active')) renderQuestPanel();
        }, 60000);
        // Warm the collection cache in the background; custom deck art on the
        // menu tile resolves once it lands.
        ensureCollection().then(() => renderMenu());
    }

    return { init, openMenu, showScreen, navBack };
}
