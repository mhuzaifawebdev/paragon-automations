/**
 * Offline test for status.js's CSV parser. No network, no Vercel, no GitHub token needed.
 *   node web/api/test_status.js
 */
const assert = require("assert");

// parseCsv is not exported (module.exports is the handler) - re-require the file as text and
// eval just the function, so this test can't silently drift from the real implementation.
const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "status.js"), "utf8");
const fnSrc = src.slice(src.indexOf("function parseCsv"), src.indexOf("\nasync function fetchCsv"));
const parseCsv = eval(`(${fnSrc.replace("function parseCsv", "function")})`);

let passed = 0, total = 0;
function check(name, got, want) {
  total++;
  try {
    assert.deepStrictEqual(got, want);
    passed++;
    console.log("ok  ", name);
  } catch (e) {
    console.log("FAIL", name, "-", e.message);
  }
}

check("empty input returns no rows", parseCsv(""), []);

check("plain CSV parses into objects keyed by header",
  parseCsv("extension,manager\n100,Nadia\n101,Felice\n"),
  [{ extension: "100", manager: "Nadia" }, { extension: "101", manager: "Felice" }]);

check("a quoted field containing a comma is not split",
  parseCsv('extension,manager\n124,"Daniya, AI dialer line"\n'),
  [{ extension: "124", manager: "Daniya, AI dialer line" }]);

check("a doubled quote inside a quoted field becomes one literal quote",
  parseCsv('extension,manager\n100,"Say ""hi"""\n'),
  [{ extension: "100", manager: 'Say "hi"' }]);

check("no trailing newline on the last row is still parsed",
  parseCsv("extension,manager\n100,Nadia"),
  [{ extension: "100", manager: "Nadia" }]);

console.log(`\n${passed}/${total} passed`);
process.exit(passed === total ? 0 : 1);
