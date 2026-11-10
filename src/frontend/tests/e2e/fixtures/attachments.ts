/** Empty attachment sets for journeys that exercise source files or feedback only. */
export function emptyAttachments(version = 0) {
  return {
    version,
    items: [],
    total_bytes: 0,
    max_file_bytes: 10 * 1024 * 1024,
    max_total_bytes: 50 * 1024 * 1024,
    max_files: 100,
  };
}
