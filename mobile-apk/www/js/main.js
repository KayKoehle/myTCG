import { createGameController } from './controller.js';
import { createMenuController } from './menu.js';
import { getUiElements } from './dom.js';

const ui = getUiElements();
const controller = createGameController(ui);
const menu = createMenuController(ui, controller);
controller.init({ onExitToMenu: () => menu.openMenu() });
menu.init();
