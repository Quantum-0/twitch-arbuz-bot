/**
 * Точка входа библиотеки веб-компонентов q0ui.
 *
 * Подключать на страницу одним тегом (статика версионируется через static_url):
 *   <script type="module" src="{{ static_url('js/ui/index.js') }}"></script>
 *
 * Документация каждого компонента — в его модуле. Общие конвенции — base.js.
 */
import "./switch.js";
import "./slider.js";
import "./number.js";
import "./indicator.js";
import "./status-card.js";

export { Q0Switch } from "./switch.js";
export { Q0Slider } from "./slider.js";
export { Q0Number } from "./number.js";
export { Q0Indicator } from "./indicator.js";
export { Q0StatusCard } from "./status-card.js";
export { bindSettingSwitches, createRewardButton } from "./bindings.js";
