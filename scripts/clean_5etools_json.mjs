import path from "path";
import {pathToFileURL} from "url";

const root = process.env.FIVETOOLS_ROOT || "/data/5etools-mirror-2.github.io";
process.chdir(root);

await import(pathToFileURL(path.join(root, "node/locale/i18n.js")).href);
await import(pathToFileURL(path.join(root, "js/parser.js")).href);
await import(pathToFileURL(path.join(root, "js/utils.js")).href);
await import(pathToFileURL(path.join(root, "js/render.js")).href);

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);

const data = JSON.parse(Buffer.concat(chunks).toString("utf8"));
process.stdout.write(CleanUtil.getCleanJson(data, {isFast: true}));
