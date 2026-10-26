// `node --test customer-pulse/canvas/tests/` loads this directory as a module on Node 22, so this entry imports every suite.
import "./canvas.test.mjs";
import "./server.test.mjs";
import "./copilot-routing.test.mjs";
