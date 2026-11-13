/* 会话范围标签：显示所选系统名，加上不属于这些系统的直接指定工程。 */

export function scopeLabel(value, applications = [], repositories = []) {
  if (!value) return "";
  const covered = new Set(
    applications.filter((item) => value.systemIds?.includes(item.systemId)).map((item) => item.repositoryId)
  );
  const names = (value.systemIds || [])
    .map((id) => applications.find((item) => item.systemId === id)?.systemName || id);
  const direct = (value.repositoryIds || [])
    .filter((id) => !covered.has(id))
    .map((id) => repositories.find((item) => item.id === id)?.displayName || id);
  return [...names, ...direct].join(" + ");
}
