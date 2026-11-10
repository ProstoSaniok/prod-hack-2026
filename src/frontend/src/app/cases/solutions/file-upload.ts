import { SolutionError, UploadedFile } from './solution.models';

export async function readUpload(file: File): Promise<UploadedFile> {
  if (file.size > 1_000_000)
    throw new SolutionError(
      'validation',
      `Файл «${file.name}» слишком большой. В деморежиме доступно до 1 МБ на файл.`,
    );
  const bytes = new Uint8Array(await file.arrayBuffer());
  const path = file.webkitRelativePath || file.name;
  try {
    if (bytes.includes(0) || /\.(pdf|zip|docx?|xlsx?|pptx?)$/i.test(file.name))
      throw new Error('Binary');
    return { path, content: new TextDecoder('utf-8', { fatal: true }).decode(bytes) };
  } catch {
    let source = '';
    for (let index = 0; index < bytes.length; index += 32_768)
      source += String.fromCharCode(...bytes.subarray(index, index + 32_768));
    return {
      path,
      content: '',
      binary: {
        base64: btoa(source),
        mimeType: file.type || 'application/octet-stream',
        size: file.size,
      },
    };
  }
}
