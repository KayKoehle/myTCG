// Lets the player temporarily hide a decision popup (the choice modal or the
// card-stack popup) to study the board before committing. Each popup opts in
// with a "👁 Board" button; a floating restore pill brings the popup back.
// The board is only meant to be read while peeking — pending choices leave no
// legal board actions, so taps behind the popup are inert.

let restoreEl = null;
const peeking = new Set();

function refreshRestore() {
    if (restoreEl) restoreEl.classList.toggle('show', peeking.size > 0);
}

// Bring every peeked popup back into view. Also called whenever a popup opens
// or closes so peek state never leaks across separate decisions.
export function unpeekAll() {
    peeking.forEach((modal) => modal.classList.remove('peeking'));
    peeking.clear();
    refreshRestore();
}

function peek(modal) {
    if (!modal) return;
    modal.classList.add('peeking');
    peeking.add(modal);
    refreshRestore();
}

export function initPeek() {
    restoreEl = document.getElementById('peekRestore');
    if (restoreEl) restoreEl.addEventListener('click', unpeekAll);
    ['choicePeek', 'stackPeek'].forEach((id) => {
        const btn = document.getElementById(id);
        if (!btn) return;
        btn.addEventListener('click', () => peek(btn.closest('.choice-modal, .stack-modal')));
    });
}
