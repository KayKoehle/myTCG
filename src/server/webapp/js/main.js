import { createCardStackPopup } from './cardstack.js';
import { createGameController } from './controller.js';
import { createMenuController } from './menu.js';
import { getUiElements } from './dom.js';

const ui = getUiElements();
// One shared card-stack popup: in-game choices and the collection's card
// reader both use it (its DOM listeners must only be bound once).
const cardStack = createCardStackPopup(ui);
const controller = createGameController(ui, cardStack);
const menu = createMenuController(ui, controller, cardStack);
controller.init({ onExitToMenu: () => menu.openMenu() });
menu.init();
