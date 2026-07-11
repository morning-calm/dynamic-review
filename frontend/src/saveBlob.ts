/** Trigger a browser download of a fetched Blob. The download endpoints need the
 * Authorization header (a plain <a href> can't send one → 401), so the blob is fetched
 * first and handed to a temporary object URL. */
export const saveBlob = (blob: Blob, filename: string): void => {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
};
