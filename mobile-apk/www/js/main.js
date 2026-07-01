import { createGameController } from './controller.js';
import { getUiElements } from './dom.js';

const ui = getUiElements();
const controller = createGameController(ui);
controller.init();
