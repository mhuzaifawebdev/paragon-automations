/**
 * Shared GitHub Contents API helpers for web_scraper/api/*.js. Same PUT/GET-via-Contents-API
 * pattern already used by webhook/worker.js (write) and web/api/status.js (read) - not a new
 * approach, just factored out since three functions here need it.
 */
function cfg() {
  const owner = process.env.GITHUB_OWNER;
  const repo = process.env.GITHUB_REPO;
  const branch = process.env.GITHUB_BRANCH || "main";
  const token = process.env.GITHUB_TOKEN;
  if (!owner || !repo || !token) {
    throw new Error("GITHUB_OWNER / GITHUB_REPO / GITHUB_TOKEN not set in Vercel environment variables.");
  }
  return { owner, repo, branch, token };
}

async function getRawFile(path) {
  const { owner, repo, branch, token } = cfg();
  const url = `https://api.github.com/repos/${owner}/${repo}/contents/${path}?ref=${branch}`;
  const resp = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      "User-Agent": "paragon-scraper-web",
      Accept: "application/vnd.github.raw",
    },
  });
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`GitHub GET ${path} failed: ${resp.status} ${await resp.text()}`);
  return resp.text();
}

async function getRawBinary(path) {
  // Same as getRawFile but returns a Buffer, not text - required for binary files (results.xlsx is
  // a zip archive; reading it as text would corrupt any byte sequence that isn't valid UTF-8).
  const { owner, repo, branch, token } = cfg();
  const url = `https://api.github.com/repos/${owner}/${repo}/contents/${path}?ref=${branch}`;
  const resp = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      "User-Agent": "paragon-scraper-web",
      Accept: "application/vnd.github.raw",
    },
  });
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`GitHub GET ${path} failed: ${resp.status} ${await resp.text()}`);
  return Buffer.from(await resp.arrayBuffer());
}

async function fileExists(path) {
  const { owner, repo, branch, token } = cfg();
  const url = `https://api.github.com/repos/${owner}/${repo}/contents/${path}?ref=${branch}`;
  const resp = await fetch(url, {
    headers: { Authorization: `Bearer ${token}`, "User-Agent": "paragon-scraper-web" },
  });
  return resp.status === 200;
}

async function putFile(path, content, message) {
  const { owner, repo, branch, token } = cfg();
  const url = `https://api.github.com/repos/${owner}/${repo}/contents/${path}`;
  const body = {
    message,
    branch,
    content: Buffer.from(content, "utf8").toString("base64"),
  };
  const resp = await fetch(url, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${token}`,
      "User-Agent": "paragon-scraper-web",
      Accept: "application/vnd.github+json",
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`GitHub PUT ${path} failed: ${resp.status} ${await resp.text()}`);
  return resp.json();
}

async function dispatch(eventType, clientPayload) {
  const { owner, repo, token } = cfg();
  const resp = await fetch(`https://api.github.com/repos/${owner}/${repo}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "User-Agent": "paragon-scraper-web",
      Accept: "application/vnd.github+json",
    },
    body: JSON.stringify({ event_type: eventType, client_payload: clientPayload }),
  });
  if (!resp.ok) throw new Error(`GitHub dispatch failed: ${resp.status} ${await resp.text()}`);
}

module.exports = { cfg, getRawFile, getRawBinary, fileExists, putFile, dispatch };
