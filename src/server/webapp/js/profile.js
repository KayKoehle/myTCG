// Player profile: crowns, deck edits, custom decks, cosmetics, statistics,
// quest progress, and progression unlocks, persisted in localStorage. This is
// purely client-side progression — the rules engine only ever sees deck names
// and card id lists.

import { DEFAULT_ELO, ELO_FLOOR } from './elo.js';

export const DECK_META = {
    the_flood: { defaultName: 'The Deluge Deck', mainCard: 'The Ark' },
    siege_of_troy: { defaultName: 'The Trojan Siege Deck', mainCard: 'The Trojan Horse' },
    epic_of_gilgamesh: { defaultName: 'The Gilgamesh Deck', mainCard: 'Gilgamesh' },
    inannas_descent: { defaultName: 'The Inanna Deck', mainCard: 'Inanna, Goddess of Love and War' },
};

export const DECK_IDS = Object.keys(DECK_META);

// Playable game modes. The menu's Play button always starts the favorite
// mode directly; the chips under it change (and persist) the favorite.
export const GAME_MODES = [
    { id: '1v1', label: '1v1', name: 'Duel', players: 2, sub: 'Classic duel vs the AI' },
    { id: 'ffa3', label: '3P', name: '3-Player FFA', players: 3, sub: 'Free-for-all vs 2 AI rivals' },
    { id: 'ffa4', label: '4P', name: '4-Player FFA', players: 4, sub: 'Free-for-all vs 3 AI rivals' },
    { id: 'ffa5', label: '5P', name: '5-Player FFA', players: 5, sub: 'Free-for-all vs 4 AI rivals' },
];

export function gameModeById(modeId) {
    return GAME_MODES.find((m) => m.id === modeId) || GAME_MODES[0];
}

// Every playable deck holds exactly this many cards.
export const DECK_SIZE = 15;

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
    { id: 'verdant', name: 'Verdant Eden', cost: 4 },
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
    { id: 'phoenix', text: '🔥 Rise from the ashes!', cost: 1 },
];

const STORAGE_KEY = 'mytcg_profile_v1';

function defaultStats() {
    return {
        elo: DEFAULT_ELO, // one rating across all game modes
        gamesPlayed: 0,
        gamesWon: 0,
        crownsEarned: 0, // lifetime total, never reduced by spending
        decks: {}, // deckId -> { games, wins }
        cards: {}, // legacy global card stats (no longer written or read)
        deckCards: {}, // deckId -> cardId -> { games, wins, played, playedWins }
    };
}

function defaultProfile() {
    return {
        crowns: 0,
        selectedDeck: 'epic_of_gilgamesh',
        favoriteMode: '1v1', // GAME_MODES id started by the Play button
        deckNames: {}, // stock deckId -> custom display name
        deckCards: {}, // stock deckId -> [cardIds], only when edited
        customDecks: {}, // deckId -> { name, cards: [cardIds] }
        deckCosmetics: {}, // deckId -> { cardBack?, board?, emotes?: [ids] }
        owned: {
            cardBacks: ['classic'],
            boards: ['classic'],
            emotes: ['good_luck', 'heart', 'good_game'],
        },
        equipped: { cardBack: 'classic', board: 'classic', emotes: ['good_luck', 'heart', 'good_game'] },
        stats: defaultStats(),
        quests: {}, // managed by quests.js: { daily: {key, items}, weekly: {key, items} }
    };
}

function loadProfile() {
    const base = defaultProfile();
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return base;
        const stored = JSON.parse(raw);
        const merged = {
            ...base,
            ...stored,
            owned: { ...base.owned, ...(stored.owned || {}) },
            equipped: { ...base.equipped, ...(stored.equipped || {}) },
            deckNames: stored.deckNames || {},
            deckCards: stored.deckCards || {},
            customDecks: stored.customDecks || {},
            deckCosmetics: stored.deckCosmetics || {},
            stats: { ...defaultStats(), ...(stored.stats || {}) },
            quests: stored.quests || {},
        };
        // Profiles from before progression tracking: crowns in the bank imply
        // finished games, so keep Decks/Shop/Quests unlocked for them.
        if (!stored.stats && (stored.crowns || 0) > 0) {
            merged.stats.gamesPlayed = 3;
            merged.stats.crownsEarned = stored.crowns;
        }
        return merged;
    } catch (error) {
        return base;
    }
}

const profile = loadProfile();

// Which deck's cosmetics are live right now (set when a match starts).
// Not persisted: reloading mid-game falls back to the defaults.
let activeCosmeticsDeckId = null;

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

// quests.js mutates profile.quests in place and persists through this.
export function persistProfile() {
    save();
}

// --- Crowns ----------------------------------------------------------------

export function getCrowns() {
    return profile.crowns;
}

export function addCrowns(count) {
    const amount = Math.max(0, Number(count) || 0);
    profile.crowns += amount;
    profile.stats.crownsEarned += amount;
    save();
}

export function spendCrowns(count) {
    if (profile.crowns < count) return false;
    profile.crowns -= count;
    save();
    return true;
}

// --- Progression unlocks -----------------------------------------------------

export function getFavoriteMode() {
    return GAME_MODES.some((m) => m.id === profile.favoriteMode) ? profile.favoriteMode : '1v1';
}

export function setFavoriteMode(modeId) {
    if (!GAME_MODES.some((m) => m.id === modeId)) return;
    profile.favoriteMode = modeId;
    save();
}

export function getStats() {
    return profile.stats;
}

// --- Elo ---------------------------------------------------------------------

export function getElo() {
    const elo = Number(profile.stats.elo);
    return Number.isFinite(elo) ? elo : DEFAULT_ELO;
}

// Applies a (possibly negative) rating change and returns the new rating.
export function applyEloDelta(delta) {
    profile.stats.elo = Math.max(ELO_FLOOR, getElo() + Math.round(Number(delta) || 0));
    save();
    return profile.stats.elo;
}

export function decksUnlocked() {
    return profile.stats.gamesPlayed >= 1;
}

export function shopUnlocked() {
    return profile.stats.crownsEarned >= 1;
}

export function questsUnlocked() {
    return profile.stats.gamesPlayed >= 3;
}

// Called once per finished game (win, loss, draw, surrender) with the deck
// the player brought. Card stats are kept per deck: a card counts a game when
// it was in that deck's list, and a play when it actually hit the board.
export function recordGameResult({ deckId, cardIds, playedCardIds, won }) {
    profile.stats.gamesPlayed += 1;
    if (won) profile.stats.gamesWon += 1;
    if (deckId) {
        const deckStats = profile.stats.decks[deckId] || { games: 0, wins: 0 };
        deckStats.games += 1;
        if (won) deckStats.wins += 1;
        profile.stats.decks[deckId] = deckStats;

        const cards = profile.stats.deckCards[deckId] || {};
        const played = new Set(playedCardIds || []);
        for (const cardId of (cardIds || [])) {
            const s = cards[cardId] || { games: 0, wins: 0, played: 0, playedWins: 0 };
            s.games += 1;
            if (won) s.wins += 1;
            if (played.has(cardId)) {
                s.played += 1;
                if (won) s.playedWins += 1;
            }
            cards[cardId] = s;
        }
        profile.stats.deckCards[deckId] = cards;
    }
    save();
}

export function deckWinRate(deckId) {
    const s = profile.stats.decks[deckId];
    if (!s || !s.games) return null;
    return { games: s.games, wins: s.wins, rate: s.wins / s.games };
}

// This card's record inside this specific deck; other decks never bleed in.
export function cardDeckStats(deckId, cardId) {
    const s = (profile.stats.deckCards[deckId] || {})[cardId];
    if (!s || !s.games) return null;
    return {
        games: s.games,
        wins: s.wins,
        rate: s.wins / s.games,
        played: s.played || 0,
        playedRate: s.played ? (s.playedWins || 0) / s.played : null,
    };
}

// --- Decks -------------------------------------------------------------------

export function isCustomDeck(deckId) {
    return Boolean(profile.customDecks[deckId]);
}

export function allDeckIds() {
    return [...DECK_IDS, ...Object.keys(profile.customDecks)];
}

export function getSelectedDeckId() {
    if (DECK_META[profile.selectedDeck] || isCustomDeck(profile.selectedDeck)) {
        return profile.selectedDeck;
    }
    return DECK_IDS[0];
}

export function selectDeck(deckId) {
    if (!DECK_META[deckId] && !isCustomDeck(deckId)) return;
    profile.selectedDeck = deckId;
    save();
}

export function createCustomDeck() {
    const number = Object.keys(profile.customDecks).length + 1;
    const deckId = `custom_${Date.now().toString(36)}${Math.floor(Math.random() * 1296).toString(36)}`;
    profile.customDecks[deckId] = { name: `My Deck ${number}`, cards: [] };
    save();
    return deckId;
}

export function deleteCustomDeck(deckId) {
    if (!isCustomDeck(deckId)) return;
    delete profile.customDecks[deckId];
    delete profile.deckCosmetics[deckId];
    delete profile.stats.decks[deckId];
    if (profile.selectedDeck === deckId) profile.selectedDeck = DECK_IDS[0];
    save();
}

export function deckDisplayName(deckId) {
    if (isCustomDeck(deckId)) return profile.customDecks[deckId].name || 'Custom deck';
    return profile.deckNames[deckId] || (DECK_META[deckId] ? DECK_META[deckId].defaultName : deckId);
}

export function renameDeck(deckId, name) {
    const trimmed = String(name || '').trim().slice(0, 30);
    if (isCustomDeck(deckId)) {
        if (trimmed) profile.customDecks[deckId].name = trimmed;
        save();
        return;
    }
    if (trimmed && trimmed !== DECK_META[deckId].defaultName) {
        profile.deckNames[deckId] = trimmed;
    } else {
        delete profile.deckNames[deckId];
    }
    save();
}

export function deckIsEdited(deckId) {
    return !isCustomDeck(deckId) && Array.isArray(profile.deckCards[deckId]);
}

// The card ids of a deck: the player's edit when there is one, otherwise the
// stock list (which the caller resolves from /api/collection).
export function deckCardIds(deckId, stockIds) {
    if (isCustomDeck(deckId)) return profile.customDecks[deckId].cards.slice();
    return deckIsEdited(deckId) ? profile.deckCards[deckId].slice() : (stockIds || []).slice();
}

export function setDeckCards(deckId, cardIds, stockIds) {
    const next = (cardIds || []).slice();
    if (isCustomDeck(deckId)) {
        profile.customDecks[deckId].cards = next;
        save();
        return;
    }
    const stock = (stockIds || []).slice();
    const isStock = stock.length === next.length && stock.slice().sort().join('|') === next.slice().sort().join('|');
    if (isStock) {
        delete profile.deckCards[deckId];
    } else {
        profile.deckCards[deckId] = next;
    }
    save();
}

export function resetDeck(deckId) {
    if (isCustomDeck(deckId)) return;
    delete profile.deckCards[deckId];
    delete profile.deckNames[deckId];
    save();
}

// What to send to the engine for this deck. Edited and custom decks go out as
// an explicit card list under a non-stock registry name so they can never
// shadow the AI's copy of the stock deck.
export function deckMatchConfig(deckId, stockIds) {
    if (isCustomDeck(deckId) || deckIsEdited(deckId)) {
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
    applyCosmetics(activeCosmeticsDeckId);
}

export function ownedEmotes() {
    return EMOTES.filter((emote) => ownsItem('emote', emote.id));
}

// The default emote loadout, equipped in the shop like card backs and boards
// (a deck's own loadout still overrides it). Always 1..MAX_ACTIVE_EMOTES
// owned ids; profiles from before this slot fall back to the first owned.
export function equippedEmoteIds() {
    const list = Array.isArray(profile.equipped.emotes) ? profile.equipped.emotes : [];
    const owned = list.filter((id) => ownsItem('emote', id));
    if (owned.length) return owned.slice(0, MAX_ACTIVE_EMOTES);
    return ownedEmotes().slice(0, MAX_ACTIVE_EMOTES).map((emote) => emote.id);
}

// Equips (or unequips) an emote in the default loadout. Returns 'equipped',
// 'unequipped', 'full' (already MAX_ACTIVE_EMOTES picked), or 'last' (the
// final emote can't be removed).
export function toggleEquippedEmote(itemId) {
    if (!ownsItem('emote', itemId)) return 'full';
    const current = equippedEmoteIds();
    if (current.includes(itemId)) {
        if (current.length <= 1) return 'last';
        profile.equipped.emotes = current.filter((id) => id !== itemId);
        save();
        return 'unequipped';
    }
    if (current.length >= MAX_ACTIVE_EMOTES) return 'full';
    profile.equipped.emotes = [...current, itemId];
    save();
    return 'equipped';
}

// --- Per-deck cosmetics --------------------------------------------------------
// A deck may override the default card back / board / emote loadout; null
// means "use my defaults". Overrides referencing unowned items are ignored.

export function deckCosmetic(deckId, kind) {
    const overrides = profile.deckCosmetics[deckId];
    const itemId = overrides ? overrides[kind] : null;
    return itemId && ownsItem(kind, itemId) ? itemId : null;
}

export function setDeckCosmetic(deckId, kind, itemId) {
    const overrides = profile.deckCosmetics[deckId] || {};
    if (itemId) {
        overrides[kind] = itemId;
    } else {
        delete overrides[kind];
    }
    if (Object.keys(overrides).length) {
        profile.deckCosmetics[deckId] = overrides;
    } else {
        delete profile.deckCosmetics[deckId];
    }
    save();
    if (activeCosmeticsDeckId === deckId) applyCosmetics(deckId);
}

export function deckEmoteLoadout(deckId) {
    const overrides = profile.deckCosmetics[deckId];
    const loadout = overrides && Array.isArray(overrides.emotes) ? overrides.emotes : null;
    if (!loadout) return null;
    const filtered = loadout.filter((id) => ownsItem('emote', id));
    return filtered.length ? filtered : null;
}

export function setDeckEmoteLoadout(deckId, emoteIds) {
    const overrides = profile.deckCosmetics[deckId] || {};
    if (Array.isArray(emoteIds) && emoteIds.length) {
        overrides.emotes = emoteIds.slice();
    } else {
        delete overrides.emotes;
    }
    if (Object.keys(overrides).length) {
        profile.deckCosmetics[deckId] = overrides;
    } else {
        delete profile.deckCosmetics[deckId];
    }
    save();
}

// At most this many emotes can be taken into a match; the deck editor
// enforces the same cap on loadouts.
export const MAX_ACTIVE_EMOTES = 3;

// The emotes available in the current match: the active deck's loadout when
// one is set, otherwise the first owned ones — capped at MAX_ACTIVE_EMOTES
// either way.
export function activeEmotes() {
    if (activeCosmeticsDeckId) {
        const loadout = deckEmoteLoadout(activeCosmeticsDeckId);
        if (loadout) return EMOTES.filter((emote) => loadout.includes(emote.id)).slice(0, MAX_ACTIVE_EMOTES);
    }
    const equipped = equippedEmoteIds();
    return EMOTES.filter((emote) => equipped.includes(emote.id)).slice(0, MAX_ACTIVE_EMOTES);
}

// Equipped cosmetics style the whole app through data attributes on <body>
// (see styles.css: body[data-cardback=...] and body[data-board=...]).
// Pass a deckId to apply that deck's overrides (match start); pass nothing to
// restore the defaults (back to the menu).
export function applyCosmetics(deckId = null) {
    activeCosmeticsDeckId = deckId;
    const cardBack = (deckId && deckCosmetic(deckId, 'cardBack')) || profile.equipped.cardBack;
    const board = (deckId && deckCosmetic(deckId, 'board')) || profile.equipped.board;
    document.body.dataset.cardback = cardBack;
    document.body.dataset.board = board;
}
