/**
 * Pure helpers for root-scoped Pi search.
 *
 * Authorized repositories are workspace symlinks. Instead of asking ripgrep/fd
 * to follow every symlink, searches start from the authorized real roots and
 * map result paths back to the workspace. This keeps repository-internal
 * symlinks from expanding the authorization scope.
 */

import { realpathSync } from "node:fs";
import path from "node:path";

function within(root, target) {
  const relative = path.relative(root, target);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

export function parseMounts(raw, allowedRoots = []) {
  let value;
  try {
    value = JSON.parse(raw || "{}");
  } catch {
    return [];
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  const allowed = allowedRoots.map((root) => path.resolve(root));
  const allowedWithin = (target) =>
    allowed.length === 0 || allowed.some((root) => within(root, target));
  const mounts = [];
  for (const [rawPrefix, rawReal] of Object.entries(value)) {
    if (typeof rawReal !== "string" || !rawReal) continue;
    const prefix = String(rawPrefix)
      .replace(/\\/g, "/")
      .replace(/^\.?\//, "")
      .replace(/\/+$/, "");
    if (!prefix || prefix.split("/").includes("..") || path.isAbsolute(prefix)) continue;
    let real;
    try {
      real = realpathSync(rawReal);
    } catch {
      continue;
    }
    if (!allowedWithin(real)) continue;
    mounts.push({ prefix, real });
  }
  const seen = new Set();
  return mounts
    .filter((mount) => !seen.has(mount.prefix) && seen.add(mount.prefix))
    .sort((left, right) => right.prefix.length - left.prefix.length);
}

export function resolveSearchTargets(workspace, mounts, rawPath) {
  const root = path.resolve(workspace);
  const requested = path.resolve(root, rawPath || ".");
  const relative = path.relative(root, requested);
  const inside = relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
  if (inside) {
    const normalized = relative.replace(/\\/g, "/");
    if (normalized === "") {
      return {
        targets: [
          { cwd: root, target: ".", prefix: "" },
          ...mounts.map((mount) => ({ cwd: mount.real, target: ".", prefix: mount.prefix })),
        ],
      };
    }
    const mount = mounts.find((item) =>
      normalized === item.prefix || normalized.startsWith(item.prefix + "/"));
    if (mount) {
      const target = normalized === mount.prefix
        ? "."
        : normalized.slice(mount.prefix.length + 1);
      return { targets: [{ cwd: mount.real, target, prefix: mount.prefix }] };
    }
    return {
      targets: [
        { cwd: root, target: normalized, prefix: "" },
        ...mounts
          .filter((item) => item.prefix.startsWith(normalized + "/"))
          .map((item) => ({ cwd: item.real, target: ".", prefix: item.prefix })),
      ],
    };
  }
  let real = requested;
  try {
    real = realpathSync(requested);
  } catch {
    // The tool reports missing paths; only authorized existing paths map here.
  }
  const mount = mounts.find((item) => within(item.real, real));
  if (!mount) {
    return { targets: [], reason: `路径不在本轮授权范围：${rawPath}` };
  }
  const target = path.relative(mount.real, real).replace(/\\/g, "/") || ".";
  return { targets: [{ cwd: mount.real, target, prefix: mount.prefix }] };
}

export function mapResultLine(line, prefix) {
  if (!line || !prefix || line === "--") return line;
  const value = line.startsWith("./") ? line.slice(2) : line;
  return `${prefix}/${value}`;
}
