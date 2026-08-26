#!/usr/bin/env node
/**
 * cdaf-skill — install the CDAF agent skill.
 *
 * Teaches AI agents to read .cdaf sidecars instead of re-processing video.
 * Zero dependencies, cross-platform (Windows / macOS / Linux).
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync, copyFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PKG_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SKILL_SRC = join(PKG_ROOT, "skills", "claude-code", "cdaf", "SKILL.md");
const VERSION = JSON.parse(readFileSync(join(PKG_ROOT, "package.json"), "utf8")).version;

const HELP = `
cdaf-skill ${VERSION} — install the CDAF agent skill

Teaches AI agents to read .cdaf sidecars (cached, hash-verified video
descriptions) instead of burning tokens re-analyzing the same footage.

Usage
  npx cdaf-skill                 Install for the current user (~/.claude/skills/cdaf)
  npx cdaf-skill --project       Install into this project (./.claude/skills/cdaf)
  npx cdaf-skill --dir <path>    Install into a specific directory
  npx cdaf-skill --print         Print the skill to stdout (for other agent frameworks)
  npx cdaf-skill --help          Show this message

Any existing SKILL.md at the destination is backed up to SKILL.md.bak.
Docs and format spec: https://github.com/UditAkhourii/cdaf
`;

function parseArgs(argv) {
  const opts = { scope: "user", dir: null, print: false, help: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--help" || a === "-h") opts.help = true;
    else if (a === "--version" || a === "-v") { console.log(VERSION); process.exit(0); }
    else if (a === "--print") opts.print = true;
    else if (a === "--project" || a === "--local") opts.scope = "project";
    else if (a === "--user" || a === "--global") opts.scope = "user";
    else if (a === "--dir") {
      opts.dir = argv[++i];
      if (!opts.dir) fail("--dir requires a path");
    } else fail(`unknown option: ${a}\nRun 'npx cdaf-skill --help' for usage.`);
  }
  return opts;
}

function fail(msg) {
  console.error(`cdaf-skill: ${msg}`);
  process.exit(1);
}

function main() {
  const opts = parseArgs(process.argv.slice(2));

  if (opts.help) {
    console.log(HELP.trim());
    return;
  }

  if (!existsSync(SKILL_SRC)) {
    fail(`packaged skill file is missing (expected at ${SKILL_SRC}).\n` +
         `This is a packaging bug — please report it at ` +
         `https://github.com/UditAkhourii/cdaf/issues`);
  }
  const skill = readFileSync(SKILL_SRC, "utf8");

  if (opts.print) {
    process.stdout.write(skill);
    return;
  }

  const targetDir = opts.dir
    ? resolve(opts.dir)
    : opts.scope === "project"
      ? join(process.cwd(), ".claude", "skills", "cdaf")
      : join(homedir(), ".claude", "skills", "cdaf");
  const targetFile = join(targetDir, "SKILL.md");

  let action = "installed";
  try {
    if (existsSync(targetFile)) {
      const current = readFileSync(targetFile, "utf8");
      if (current === skill) {
        console.log(`✓ CDAF skill already up to date\n  ${targetFile}`);
        return;
      }
      copyFileSync(targetFile, `${targetFile}.bak`);
      action = "updated";
    }
    mkdirSync(targetDir, { recursive: true });
    writeFileSync(targetFile, skill, "utf8");
  } catch (err) {
    fail(`could not write to ${targetFile}\n  ${err.message}`);
  }

  console.log(`✓ CDAF skill ${action}`);
  console.log(`  ${targetFile}`);
  if (action === "updated") console.log(`  (previous version saved as SKILL.md.bak)`);
  console.log();
  console.log(`  Agents will now check for a .cdaf sidecar before analyzing any video,`);
  console.log(`  verify it against the video's hash, and read it instead of watching.`);
  console.log();
  console.log(`  Start a new Claude Code session to pick it up.`);
  console.log(`  Generate sidecars with:  pip install cdaf[generate] && cdaf generate ./footage`);
}

main();
