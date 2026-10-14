// Offline tests for the decision rules in apps_script/LogoutWatch.gs. Run: node tests/test_logout_watch.mjs
import fs from "node:fs";
import vm from "node:vm";

const src = fs.readFileSync(new URL("../apps_script/LogoutWatch.gs", import.meta.url), "utf8");
const g = {};
vm.createContext(g);
vm.runInContext(src, g);
const { decide, inShift, minutesBetween } = g;

let pass = 0, fail = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  ok ? pass++ : fail++;
  console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : `: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`));
};
const at = s => new Date(`2026-10-14T${s}:00Z`);
const on = { online: true, day: "2026-10-14", since: "2026-10-14T07:00:00.000Z", row: null };
const away = { online: false, day: "2026-10-14", since: "2026-10-14T10:02:00.000Z", row: 7 };

check("shift window", [inShift(at("06:59"), "07:00", "16:00"), inShift(at("07:00"), "07:00", "16:00"), inShift(at("16:01"), "07:00", "16:00")], [false, true, false]);
check("first check ever: status remembered, no row", decide(null, false, at("09:00"), true).action, "none");
check("first check of a new day: someone already offline is not a log-out", decide({ ...on, day: "2026-10-13" }, false, at("07:00"), true).action, "none");
check("online -> offline in the shift is a log-out", decide(on, false, at("10:02"), true).action, "logout");
check("online -> offline after the shift is not", decide(on, false, at("16:30"), false).action, "none");
check("no answer from Zadarma: nothing happens, nothing saved", JSON.stringify(decide(on, null, at("10:02"), true)), JSON.stringify({ action: "none", next: null }));
check("no change: nothing saved", decide(on, true, at("10:02"), true).next, null);
check("offline -> online with an open log-out closes it", decide(away, true, at("10:31"), true).action, "login");
check("and clears the open row", decide(away, true, at("10:31"), true).next.row, null);
check("minutes away counted from the log-out", minutesBetween(away.since, at("10:31")), 29);
check("coming back after the shift still closes the row", decide(away, true, at("16:40"), false).action, "login");
check("offline -> online with no open log-out writes nothing", decide({ ...away, row: null }, true, at("10:31"), true).action, "none");
check("away overnight: the open row is kept into the next day", decide({ ...away, day: "2026-10-13" }, false, at("07:00"), true).next.row, 7);
check("and closed when they log in the next day", decide({ ...away, day: "2026-10-13" }, true, at("07:05"), true).action, "login");

console.log(`\n${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);
