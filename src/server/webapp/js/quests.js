// Daily quests, weekly quests, and weekend events.
//
// Rotation is deterministic: the local date picks the day's daily quests, the
// week's Monday picks the weekly quests and the weekend event, so the same
// player always sees the same set until the period rolls over. Progress lives
// in the profile (localStorage) and resets with the period key.
//
// The game controller feeds events in (round won, card played, game finished);
// completing a quest pays its crown reward immediately and pops a toast.

import { addCrowns, getProfile, persistProfile, questsUnlocked } from './profile.js';
import { showToast } from './helpers.js';

// counts(ctx) returns how much progress one event is worth (0 = no progress).
const DAILY_POOL = [
    { id: 'd_win1', title: 'Win a game', reward: 10, target: 1, event: 'game', counts: (ctx) => (ctx.won ? 1 : 0) },
    { id: 'd_flawless', title: 'Win a game 4:0', reward: 15, target: 1, event: 'game', counts: (ctx) => (ctx.flawless ? 1 : 0) },
    { id: 'd_play3', title: 'Play 3 games', reward: 10, target: 3, event: 'game', counts: () => 1 },
    { id: 'd_rounds5', title: 'Win 5 rounds', reward: 10, target: 5, event: 'round', counts: () => 1 },
    { id: 'd_cards12', title: 'Play 12 cards', reward: 8, target: 12, event: 'card', counts: () => 1 },
    { id: 'd_win2', title: 'Win 2 games', reward: 18, target: 2, event: 'game', counts: (ctx) => (ctx.won ? 1 : 0) },
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

const DAILY_COUNT = 3;
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

// Milliseconds until the next daily quest rotation (local midnight).
export function msUntilDailyReset(now = new Date()) {
    const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
    return next.getTime() - now.getTime();
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

function ensureRotation() {
    const profile = getProfile();
    const quests = profile.quests || {};
    const nextDaily = rotatedSlot(quests.daily, dayKey(), DAILY_POOL, DAILY_COUNT, 'daily');
    const nextWeekly = rotatedSlot(quests.weekly, weekKey(), WEEKLY_POOL, WEEKLY_COUNT, 'weekly');
    if (quests.daily !== nextDaily || quests.weekly !== nextWeekly) {
        profile.quests = { daily: nextDaily, weekly: nextWeekly };
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

// Everything the menu needs to render the quest panel.
export function getQuestBoard() {
    const state = ensureRotation();
    const attach = (items, pool) => items
        .map((item) => ({ ...item, def: defById(pool, item.id) }))
        .filter((item) => item.def);
    return {
        unlocked: questsUnlocked(),
        daily: attach(state.daily.items, DAILY_POOL),
        weekly: attach(state.weekly.items, WEEKLY_POOL),
        weekend: currentWeekendEvent(),
        dailyResetMs: msUntilDailyReset(),
        weeklyResetMs: msUntilWeeklyReset(),
    };
}

// --- Event handling ------------------------------------------------------------

function questRewardMultiplier() {
    const { def, active } = currentWeekendEvent();
    return active && def.questMultiplier ? def.questMultiplier : 1;
}

function applyEvent(eventKind, ctx) {
    // Quests only progress once they are visible to the player.
    if (!questsUnlocked()) return;
    const state = ensureRotation();
    const slots = [
        { slot: state.daily, pool: DAILY_POOL, label: 'Daily quest' },
        { slot: state.weekly, pool: WEEKLY_POOL, label: 'Weekly quest' },
    ];
    let changed = false;
    for (const { slot, pool, label } of slots) {
        for (const item of slot.items) {
            const def = defById(pool, item.id);
            if (!def || def.event !== eventKind || item.done) continue;
            const gained = def.counts(ctx || {});
            if (!gained) continue;
            item.progress = Math.min(def.target, item.progress + gained);
            changed = true;
            if (item.progress >= def.target) {
                item.done = true;
                const reward = def.reward * questRewardMultiplier();
                addCrowns(reward);
                showToast(`${label} complete: ${def.title} — +${reward} crowns!`, 'gold');
            }
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
