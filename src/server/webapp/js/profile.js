// Player profile: crowns, deck edits, and cosmetics, persisted in
// localStorage. This is purely client-side progression — the rules engine
// only ever sees deck names and card id lists.

export const DECK_META = {
    the_flood: { defaultName: 'The Deluge Deck', mainCard: 'The Ark' },
    siege_of_troy: { defaultName: 'The Trojan Siege Deck', mainCard: 'The Trojan Horse' },
    epic_of_gilgamesh: { defaultName: 'The Gilgamesh Deck', mainCard: 'Gilgamesh' },
    inannas_descent: { defaultName: 'The Inanna Deck', mainCard: 'Inanna, Goddess of Love and War' },
};

export const DECK_IDS = Object.keys(DECK_META);

// --- Shop catalog ----------------------------------------------------------
// Costs are deliberately tiny for now (debug pricing): one won game earns up
// to 4 crowns, enough for a couple of items.

export const CARD_BACKS = [
    { id: 'classic', name: 'Classic Lapis', cost: 0 },
    { id: 'cedar', name: 'Cedar Forest', cost: 1 },
    { id: 'ember', name: 'Ember of Kingu', cost: 1 },
    { id: 'aegean', name: 'Aegean Tide', cost: 2 },
    { id: 'royal', name: 'Royal Gold', cost: 2 },
    { id: 'obsidian', name: 'Obsidian Storm', cost: 3 },
    { id: 'bronze', name: 'Bronze Aegis', cost: 3 },
    { id: 'starlit', name: 'Starlit Uruk', cost: 3 },
    { id: 'crimson', name: 'Crimson Ziggurat', cost: 4 },
];

export const BOARDS = [
    { id: 'classic', name: 'Midnight Ziggurat', cost: 0 },
    { id: 'dawn', name: 'Desert Dawn', cost: 1 },
    { id: 'underworld', name: 'The Underworld', cost: 2 },
    { id: 'sea', name: 'Wine-Dark Sea', cost: 2 },
];

export const EMOTES = [
    { id: 'good_luck', text: '🍀 Good luck!', cost: 0 },
    { id: 'heart', text: '❤️', cost: 0 },
    { id: 'good_game', text: '🤝 Good game!', cost: 0 },
    { id: 'crown', text: '👑 Bow before the king!', cost: 1 },
    { id: 'flood', text: '🌊 The flood is coming!', cost: 1 },
    { id: 'laugh', text: '😆', cost: 1 },
    { id: 'curses', text: '😤 Curses!', cost: 1 },
    { id: 'trojan_horse', text: '🐎 Beware Greeks bearing gifts!', cost: 1 },
    { id: 'thunder', text: '⚡ By the gods!', cost: 1 },
    { id: 'shield', text: '🛡️ Hold the line!', cost: 1 },
    { id: 'skull', text: '💀 To the underworld with you!', cost: 1 },
];

const STORAGE_KEY = 'mytcg_profile_v1';

function defaultProfile() {
    return {
        crowns: 0,
        selectedDeck: 'epic_of_gilgamesh',
        deckNames: {}, // deckId -> custom display name
        deckCards: {}, // deckId -> [cardIds], only when edited
        owned: {
            cardBacks: ['classic'],
            boards: ['classic'],
            emotes: ['good_luck', 'heart', 'good_game'],
        },
        equipped: { cardBack: 'classic', board: 'classic' },
    };
}

function loadProfile() {
    const base = defaultProfile();
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return base;
        const stored = JSON.parse(raw);
        return {
            ...base,
            ...stored,
            owned: { ...base.owned, ...(stored.owned || {}) },
            equipped: { ...base.equipped, ...(stored.equipped || {}) },
            deckNames: stored.deckNames || {},
            deckCards: stored.deckCards || {},
        };
    } catch (error) {
        return base;
    }
}

const profile = loadProfile();

function save() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(profile));
    } catch (error) {
        // Storage full or unavailable: play on without persistence.
    }
}

export function getProfile() {
    return profile;
}

// --- Crowns ----------------------------------------------------------------

export function getCrowns() {
    return profile.crowns;
}

export function addCrowns(count) {
    profile.crowns += Math.max(0, Number(count) || 0);
    save();
}

export function spendCrowns(count) {
    if (profile.crowns < count) return false;
    profile.crowns -= count;
    save();
    return true;
}

// --- Decks -------------------------------------------------------------------

export function getSelectedDeckId() {
    return DECK_META[profile.selectedDeck] ? profile.selectedDeck : DECK_IDS[0];
}

export function selectDeck(deckId) {
    if (!DECK_META[deckId]) return;
    profile.selectedDeck = deckId;
    save();
}

export function deckDisplayName(deckId) {
    return profile.deckNames[deckId] || (DECK_META[deckId] ? DECK_META[deckId].defaultName : deckId);
}

export function renameDeck(deckId, name) {
    const trimmed = String(name || '').trim().slice(0, 30);
    if (trimmed && trimmed !== DECK_META[deckId].defaultName) {
        profile.deckNames[deckId] = trimmed;
    } else {
        delete profile.deckNames[deckId];
    }
    save();
}

export function deckIsEdited(deckId) {
    return Array.isArray(profile.deckCards[deckId]);
}

// The card ids of a deck: the player's edit when there is one, otherwise the
// stock list (which the caller resolves from /api/collection).
export function deckCardIds(deckId, stockIds) {
    return deckIsEdited(deckId) ? profile.deckCards[deckId].slice() : (stockIds || []).slice();
}

export function setDeckCards(deckId, cardIds, stockIds) {
    const stock = (stockIds || []).slice();
    const next = (cardIds || []).slice();
    const isStock = stock.length === next.length && stock.slice().sort().join('|') === next.slice().sort().join('|');
    if (isStock) {
        delete profile.deckCards[deckId];
    } else {
        profile.deckCards[deckId] = next;
    }
    save();
}

export function resetDeck(deckId) {
    delete profile.deckCards[deckId];
    delete profile.deckNames[deckId];
    save();
}

// What to send to the engine for this deck. Edited decks go out as an
// explicit card list under a non-stock registry name so they can never
// shadow the AI's copy of the stock deck.
export function deckMatchConfig(deckId, stockIds) {
    if (deckIsEdited(deckId)) {
        return { name: `${deckId}__custom`, cards: deckCardIds(deckId, stockIds) };
    }
    return { name: deckId, cards: null };
}

// --- Cosmetics ---------------------------------------------------------------

const OWNED_KEY_BY_KIND = { cardBack: 'cardBacks', board: 'boards', emote: 'emotes' };

export function ownsItem(kind, itemId) {
    const list = profile.owned[OWNED_KEY_BY_KIND[kind]] || [];
    return list.includes(itemId);
}

export function buyItem(kind, itemId, cost) {
    if (ownsItem(kind, itemId)) return true;
    if (!spendCrowns(cost)) return false;
    profile.owned[OWNED_KEY_BY_KIND[kind]].push(itemId);
    save();
    return true;
}

export function equippedItem(kind) {
    return profile.equipped[kind === 'cardBack' ? 'cardBack' : 'board'];
}

export function equipItem(kind, itemId) {
    if (!ownsItem(kind, itemId)) return;
    if (kind === 'cardBack') profile.equipped.cardBack = itemId;
    if (kind === 'board') profile.equipped.board = itemId;
    save();
    applyCosmetics();
}

export function ownedEmotes() {
    return EMOTES.filter((emote) => ownsItem('emote', emote.id));
}

// Equipped cosmetics style the whole app through data attributes on <body>
// (see styles.css: body[data-cardback=...] and body[data-board=...]).
export function applyCosmetics() {
    document.body.dataset.cardback = profile.equipped.cardBack;
    document.body.dataset.board = profile.equipped.board;
}
