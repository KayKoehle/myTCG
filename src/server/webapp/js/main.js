import { createCardStackPopup } from './cardstack.js';
import { createGameController } from './controller.js';
import { createMenuController } from './menu.js';
import { getUiElements } from './dom.js';
import { initPeek } from './peek.js';
import { initUpdateCheck } from './update.js';

const ui = getUiElements();
// Wire the "👁 Board" peek buttons on the decision popups (choice + card stack).
initPeek();
// One shared card-stack popup: in-game choices and the collection's card
// reader both use it (its DOM listeners must only be bound once).
const cardStack = createCardStackPopup(ui);
const controller = createGameController(ui, cardStack);
const menu = createMenuController(ui, controller, cardStack);
// The in-game home button pops the history entry the match pushed, so the
// hardware back stack stays consistent with what is on screen.
controller.init({ onExitToMenu: () => menu.navBack() });
menu.init();
// Best-effort: inside the Android app, notify if a newer APK has been released.
// No-op in the browser and offline.
initUpdateCheck();
