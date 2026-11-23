// Il server delle segnalazioni di Jarvis (Cloudflare Worker): tutta la logica sta in logica.js.
import { handle } from "./logica.js";

export default {
  fetch: (request, env) => handle(request, env, fetch),
};
