// Full-size look at a card back. Face-down cards are thumbnails everywhere they
// appear — the deck piles and the opponent's hand in a match, the tiles in the
// shop — so tapping one blows the art up until the next tap dismisses it.
//
// Tap targets opt in with data-cardback-view: "player" (whatever the player has
// equipped, per deck), "opponent", or a card back id straight from the catalog
// (the shop knows which item a tile shows). Binding is delegated from the
// document so re-rendered board and shop markup needs no rebinding.
import { CARD_BACKS } from './profile.js';

// The AI opponent always shows the default back, whatever the player equipped
// for themself (--card-back-opponent in styles.css).
const OPPONENT_BACK_ID = 'classic';

function resolveBackId(token) {
    if (token === 'opponent') return OPPONENT_BACK_ID;
    if (!token || token === 'player') return document.body.dataset.cardback || OPPONENT_BACK_ID;
    return token;
}

function backName(backId) {
    const item = CARD_BACKS.find((back) => back.id === backId);
    return item ? item.name : 'Card back';
}

export function initCardBackViewer(ui) {
    const modal = ui.cardBackViewer;
    if (!modal) return;

    function open(token) {
        const backId = resolveBackId(token);
        // The art is a CSS background switched by [data-cardback], exactly like
        // the board's own card backs — no image list to keep in sync here.
        ui.cardBackViewerArt.dataset.cardback = backId;
        ui.cardBackViewerName.textContent = backName(backId);
        modal.classList.add('open');
        modal.setAttribute('aria-hidden', 'false');
    }

    function close() {
        modal.classList.remove('open');
        modal.setAttribute('aria-hidden', 'true');
    }

    document.addEventListener('click', (event) => {
        const target = event.target.closest('[data-cardback-view]');
        if (!target) return;
        open(target.dataset.cardbackView);
    });

    // Anywhere on the overlay closes it, the blown-up card included.
    modal.addEventListener('click', close);
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && modal.classList.contains('open')) {
            close();
            return;
        }
        // Desktop/keyboard: the tap targets are role="button", so Enter and
        // Space have to open them the way a real button would.
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const target = event.target.closest ? event.target.closest('[data-cardback-view][role="button"]') : null;
        if (!target) return;
        event.preventDefault();
        open(target.dataset.cardbackView);
    });
}
