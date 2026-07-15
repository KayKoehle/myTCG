// Rolling quests, weekly quests, and weekend events.
//
// Rolling quests work like Marvel Snap's missions: up to QUEST_SLOTS active
// at once, and a fresh quest arrives every QUEST_INTERVAL_MS (2 hours). A
// finished quest pays out and leaves the board immediately — the panel only
// ever shows work left to do, plus when the next quest lands. If every slot
// is taken when the timer fires, the new quest waits for a free slot.
// Weekly quests still rotate deterministically off the week's Monday (same
// set on every device), as does the weekend event. Progress lives in the
// profile (localStorage).
//
// The game controller feeds events in (round won, card played, game finished);
// completing a quest pays its crown reward immediately and pops a toast.

import { addCrowns, getProfile, persistProfile, questsUnlocked } from './profile.js';
import { showToast } from './helpers.js';

// counts(ctx) returns how much progress one event is worth (0 = no progress).
// Defs with `streak: true` instead track ctx.streak as absolute progress —
// it can go back down to 0 when a loss breaks the streak.
const ROLLING_POOL = [
    { id: 'd_win1', title: 'Win a game', reward: 10, target: 1, event: 'game', counts: (ctx) => (ctx.won ? 1 : 0) },
    { id: 'd_flawless', title: 'Win a game 4:0', reward: 15, target: 1, event: 'game', counts: (ctx) => (ctx.flawless ? 1 : 0) },
    { id: 'd_play3', title: 'Play 3 games', reward: 10, target: 3, event: 'game', counts: () => 1 },
    { id: 'd_rounds5', title: 'Win 5 rounds', reward: 10, target: 5, event: 'round', counts: () => 1 },
    { id: 'd_cards12', title: 'Play 12 cards', reward: 8, target: 12, event: 'card', counts: () => 1 },
    { id: 'd_win2', title: 'Win 2 games', reward: 18, target: 2, event: 'game', counts: (ctx) => (ctx.won ? 1 : 0) },
    { id: 'd_streak2', title: 'Win 2 games in a row', reward: 20, target: 2, event: 'game', streak: true },
    { id: 'd_beings6', title: 'Play 6 beings', reward: 10, target: 6, event: 'card', counts: (ctx) => (ctx.isBeing ? 1 : 0) },
    { id: 'd_heroes2', title: 'Play 2 heroes', reward: 12, target: 2, event: 'card', counts: (ctx) => (ctx.isHero ? 1 : 0) },
    { id: 'd_banish2', title: 'Banish 2 enemy beings', reward: 12, target: 2, event: 'banish', counts: () => 1 },
    { id: 'd_revive2', title: 'Revive 2 beings', reward: 12, target: 2, event: 'revive', counts: () => 1 },
    { id: 'd_move3', title: 'Move beings 3 times', reward: 10, target: 3, event: 'move', counts: () => 1 },
    { id: 'd_draw10', title: 'Draw 10 cards', reward: 8, target: 10, event: 'draw', counts: () => 1 },
    { id: 'd_power12', title: 'Reach 12 power on one location', reward: 12, target: 1, event: 'power', counts: (ctx) => ((ctx.power || 0) >= 12 ? 1 : 0) },
    { id: 'd_monster1', title: 'Defeat a monster', reward: 15, target: 1, event: 'monster', counts: () => 1 },
];

const WEEKLY_POOL = [
    { id: 'w_win7', title: 'Win 7 games', reward: 50, target: 7, event: 'game', counts: (ctx) => (ctx.won ? 1 : 0) },
    { id: 'w_play12', title: 'Play 12 games', reward: 40, target: 12, event: 'game', counts: () => 1 },
    { id: 'w_rounds20', title: 'Win 20 rounds', reward: 45, target: 20, event: 'round', counts: () => 1 },
    { id: 'w_flawless3', title: 'Win 3 games 4:0', reward: 60, target: 3, event: 'game', counts: (ctx) => (ctx.flawless ? 1 : 0) },
    { id: 'w_streak3', title: 'Win 3 games in a row', reward: 60, target: 3, event: 'game', streak: true },
    { id: 'w_streak5', title: 'Win 5 games in a row', reward: 80, target: 5, event: 'game', streak: true },
    { id: 'w_cards40', title: 'Play 40 cards', reward: 35, target: 40, event: 'card', counts: () => 1 },
    { id: 'w_beings25', title: 'Play 25 beings', reward: 45, target: 25, event: 'card', counts: (ctx) => (ctx.isBeing ? 1 : 0) },
    { id: 'w_heroes8', title: 'Play 8 heroes', reward: 45, target: 8, event: 'card', counts: (ctx) => (ctx.isHero ? 1 : 0) },
    { id: 'w_banish8', title: 'Banish 8 enemy beings', reward: 50, target: 8, event: 'banish', counts: () => 1 },
    { id: 'w_revive6', title: 'Revive 6 beings', reward: 50, target: 6, event: 'revive', counts: () => 1 },
    { id: 'w_move10', title: 'Move beings 10 times', reward: 45, target: 10, event: 'move', counts: () => 1 },
    { id: 'w_draw40', title: 'Draw 40 cards', reward: 35, target: 40, event: 'draw', counts: () => 1 },
    { id: 'w_power16', title: 'Reach 16 power on one location', reward: 55, target: 1, event: 'power', counts: (ctx) => ((ctx.power || 0) >= 16 ? 1 : 0) },
    { id: 'w_monsters4', title: 'Defeat 4 monsters', reward: 60, target: 4, event: 'monster', counts: () => 1 },
];

// Weekend events are passive modifiers, active Saturday and Sunday.
const WEEKEND_EVENTS = [
    {
        id: 'we_double',
        name: 'Double Crowns',
        desc: 'Every round crown you win pays double this weekend.',
        roundBonus: () => 1,
    },
    {
        id: 'we_bounty',
        name: "Victor's Bounty",
        desc: '+5 bonus crowns for every game you win this weekend.',
        gameBonus: (ctx) => (ctx.won ? 5 : 0),
    },
    {
        id: 'we_flawless',
        name: 'Flawless Glory',
        desc: '4:0 victories award +10 bonus crowns this weekend.',
        gameBonus: (ctx) => (ctx.flawless ? 10 : 0),
    },
    {
        id: 'we_frenzy',
        name: 'Quest Frenzy',
        desc: 'Quest rewards are doubled this weekend.',
        questMultiplier: 2,
    },
];

// Marvel Snap-style rolling slots: up to this many quests active at once,
// with a fresh one arriving every interval.
export const QUEST_SLOTS = 3;
export const QUEST_INTERVAL_MS = 2 * 60 * 60 * 1000; // a new quest every 2h

const WEEKLY_COUNT = 3;

// --- Period keys ---------------------------------------------------------------

function pad2(n) {
    return String(n).padStart(2, '0');
}

function dayKey(date = new Date()) {
    return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

// The Monday of the current week identifies both the weekly quests and the
// upcoming/current weekend's event.
function weekKey(date = new Date()) {
    const monday = new Date(date);
    const offset = (monday.getDay() + 6) % 7; // Mon=0 ... Sun=6
    monday.setDate(monday.getDate() - offset);
    return dayKey(monday);
}

// Bonuses only apply when it is actually the weekend today.
function isWeekend(date = new Date()) {
    const day = date.getDay();
    return day === 6 || day === 0; // Sat, Sun
}

// Milliseconds until the next weekly rotation (Monday 00:00 local time).
export function msUntilWeeklyReset(now = new Date()) {
    const daysToMonday = ((8 - now.getDay()) % 7) || 7; // next Monday, never today
    const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + daysToMonday);
    return next.getTime() - now.getTime();
}

function hashString(text) {
    let h = 2166136261;
    for (let i = 0; i < text.length; i += 1) {
        h ^= text.charCodeAt(i);
        h = Math.imul(h, 16777619);
    }
    return h >>> 0;
}

// Deterministically pick `count` distinct entries from the pool.
function pickFromPool(pool, seedText, count) {
    const picked = [];
    const available = pool.slice();
    let seed = hashString(seedText);
    while (picked.length < count && available.length) {
        seed = hashString(`${seed}`);
        picked.push(available.splice(seed % available.length, 1)[0]);
    }
    return picked;
}

// --- Quest state ------------------------------------------------------------------

function rotatedSlot(slot, key, pool, count, seedPrefix) {
    const picked = pickFromPool(pool, `${seedPrefix}:${key}`, count);
    if (slot && slot.key === key && Array.isArray(slot.items)) {
        if (slot.items.length >= count) return slot;
        // The quest count grew mid-period: top up without resetting progress.
        const have = new Set(slot.items.map((item) => item.id));
        const extra = picked
            .filter((def) => !have.has(def.id))
            .slice(0, count - slot.items.length)
            .map((def) => ({ id: def.id, progress: 0, done: false }));
        return { key, items: [...slot.items, ...extra] };
    }
    return {
        key,
        items: picked.map((def) => ({ id: def.id, progress: 0, done: false })),
    };
}

// A fresh quest for the rolling board: any pool entry not already active.
// Seeded by the spawn time so re-renders in the same tick agree.
function pickRollingQuest(rolling) {
    const active = new Set(rolling.items.map((item) => item.id));
    const available = ROLLING_POOL.filter((def) => !active.has(def.id));
    if (!available.length) return null;
    return available[hashString(`quest:${rolling.nextAt}:${rolling.items.length}`) % available.length];
}

// Advance the rolling board to `now`: spawn every quest that has become due
// while slots are free. Returns true when something changed.
function refreshRolling(rolling, now) {
    let changed = false;
    while (rolling.items.length < QUEST_SLOTS && rolling.nextAt <= now) {
        const def = pickRollingQuest(rolling);
        if (!def) break;
        rolling.items.push({ id: def.id, progress: 0 });
        // Catching up after a long absence still fills at most the free
        // slots; the clock never drifts more than one interval into the past.
        rolling.nextAt = Math.max(rolling.nextAt + QUEST_INTERVAL_MS, now - QUEST_INTERVAL_MS);
        changed = true;
    }
    // Every slot taken with the timer expired: one quest is ready and
    // waiting — it appears the moment a slot frees up. Hold the clock at
    // `now` so no more than one quest is ever banked.
    if (rolling.items.length >= QUEST_SLOTS && rolling.nextAt < now) {
        rolling.nextAt = now;
        changed = true;
    }
    return changed;
}

function ensureRotation() {
    const profile = getProfile();
    const quests = profile.quests || {};
    const now = Date.now();
    let rolling = quests.rolling;
    let rollingChanged = false;
    if (!rolling || !Array.isArray(rolling.items) || !Number.isFinite(rolling.nextAt)) {
        // First run (or migration from the old daily rotation): start with a
        // full board and the 2h clock ticking toward the next quest.
        rolling = { items: [], nextAt: now };
        while (rolling.items.length < QUEST_SLOTS) {
            const def = pickRollingQuest(rolling);
            if (!def) break;
            rolling.items.push({ id: def.id, progress: 0 });
        }
        rolling.nextAt = now + QUEST_INTERVAL_MS;
        rollingChanged = true;
    }
    rollingChanged = refreshRolling(rolling, now) || rollingChanged;
    const nextWeekly = rotatedSlot(quests.weekly, weekKey(), WEEKLY_POOL, WEEKLY_COUNT, 'weekly');
    if (rollingChanged || quests.rolling !== rolling || quests.weekly !== nextWeekly) {
        profile.quests = { rolling, weekly: nextWeekly };
        persistProfile();
    }
    return profile.quests;
}

function defById(pool, id) {
    return pool.find((def) => def.id === id) || null;
}

export function currentWeekendEvent() {
    const def = WEEKEND_EVENTS[hashString(`weekend:${weekKey()}`) % WEEKEND_EVENTS.length];
    return { def, active: isWeekend() };
}

// Banner label for the weekend event: on a weekday the event is an upcoming
// preview, never "this weekend is live".
export function weekendBannerLabel(now = new Date()) {
    if (isWeekend(now)) return 'Weekend event — live now';
    const daysToSaturday = (6 - now.getDay() + 7) % 7;
    if (daysToSaturday === 1) return 'Weekend event — starts tomorrow';
    return `Weekend event — starts in ${daysToSaturday} days`;
}

// Everything the menu needs to render the quest panel. Completed quests are
// never listed: rolling quests leave the board the moment they pay out, and
// finished weekly quests are filtered here — the panel shows when the next
// quest arrives instead.
export function getQuestBoard() {
    const state = ensureRotation();
    const attach = (items, pool) => items
        .map((item) => ({ ...item, def: defById(pool, item.id) }))
        .filter((item) => item.def);
    const weekly = attach(state.weekly.items, WEEKLY_POOL);
    const rollingFull = state.rolling.items.length >= QUEST_SLOTS;
    const nextQuestMs = Math.max(0, state.rolling.nextAt - Date.now());
    return {
        unlocked: questsUnlocked(),
        rolling: attach(state.rolling.items, ROLLING_POOL),
        // The next rolling quest: how long until it arrives, and whether it
        // is already waiting for a slot to open up.
        nextQuestMs,
        nextQuestWaiting: rollingFull && nextQuestMs === 0,
        weekly: weekly.filter((item) => !item.done),
        weeklyDoneCount: weekly.filter((item) => item.done).length,
        weekend: currentWeekendEvent(),
        weeklyResetMs: msUntilWeeklyReset(),
    };
}

// --- Event handling ------------------------------------------------------------

function questRewardMultiplier() {
    const { def, active } = currentWeekendEvent();
    return active && def.questMultiplier ? def.questMultiplier : 1;
}

// One quest item's reaction to an event. Returns 'done' | 'changed' | null.
function progressItem(item, def, eventKind, ctx) {
    if (!def || def.event !== eventKind || item.done) return null;
    let changed = false;
    if (def.streak) {
        // Streak quests track the current win streak as absolute progress —
        // a loss knocks the bar back down to zero.
        const next = Math.min(def.target, Math.max(0, Number(ctx.streak) || 0));
        if (next === item.progress) return null;
        item.progress = next;
        changed = true;
    } else {
        const gained = def.counts(ctx || {});
        if (!gained) return null;
        item.progress = Math.min(def.target, item.progress + gained);
        changed = true;
    }
    return item.progress >= def.target ? 'done' : (changed ? 'changed' : null);
}

function completeQuest(def, label) {
    const reward = def.reward * questRewardMultiplier();
    addCrowns(reward);
    showToast(`${label} complete: ${def.title} — +${reward} crowns!`, 'gold');
}

function applyEvent(eventKind, ctx) {
    // Quests only progress once they are visible to the player.
    if (!questsUnlocked()) return;
    const state = ensureRotation();
    let changed = false;

    // Rolling quests leave the board as soon as they pay out; the freed slot
    // is refilled by the 2h clock (or immediately, if a quest was waiting).
    const keep = [];
    for (const item of state.rolling.items) {
        const def = defById(ROLLING_POOL, item.id);
        const result = progressItem(item, def, eventKind, ctx || {});
        if (result === 'done') {
            completeQuest(def, 'Quest');
            changed = true;
            continue;
        }
        if (result === 'changed') changed = true;
        keep.push(item);
    }
    if (keep.length !== state.rolling.items.length) {
        state.rolling.items = keep;
        refreshRolling(state.rolling, Date.now());
    }

    // Weekly quests keep their slot for the week (no refill mid-period);
    // finished ones are simply not shown anymore.
    for (const item of state.weekly.items) {
        const def = defById(WEEKLY_POOL, item.id);
        const result = progressItem(item, def, eventKind, ctx || {});
        if (result === 'done') {
            item.done = true;
            completeQuest(def, 'Weekly quest');
            changed = true;
        } else if (result === 'changed') {
            changed = true;
        }
    }
    if (changed) persistProfile();
}

// Weekend bonuses on top of the regular crown flow.
function weekendRoundBonus() {
    const { def, active } = currentWeekendEvent();
    if (!active || !def.roundBonus) return 0;
    return def.roundBonus();
}

function weekendGameBonus(ctx) {
    const { def, active } = currentWeekendEvent();
    if (!active || !def.gameBonus) return 0;
    return def.gameBonus(ctx || {});
}

// --- Hooks for the game controller ------------------------------------------------

export function questOnRoundWon() {
    const bonus = weekendRoundBonus();
    if (bonus > 0) addCrowns(bonus);
    applyEvent('round', {});
}

// ctx may carry { isBeing, isHero } for the typed play quests.
export function questOnCardPlayed(ctx) {
    applyEvent('card', ctx || {});
}

export function questOnCardBanished() {
    applyEvent('banish', {});
}

export function questOnCardRevived() {
    applyEvent('revive', {});
}

export function questOnCardMoved() {
    applyEvent('move', {});
}

export function questOnCardDrawn() {
    applyEvent('draw', {});
}

export function questOnMonsterDefeated() {
    applyEvent('monster', {});
}

// Called with the player's best single-location power after each change.
export function questOnPowerReached(power) {
    applyEvent('power', { power });
}

export function questOnGameFinished(ctx) {
    const bonus = weekendGameBonus(ctx);
    if (bonus > 0) {
        addCrowns(bonus);
        const { def } = currentWeekendEvent();
        showToast(`${def.name}: +${bonus} bonus crowns!`, 'gold');
    }
    applyEvent('game', ctx);
}
