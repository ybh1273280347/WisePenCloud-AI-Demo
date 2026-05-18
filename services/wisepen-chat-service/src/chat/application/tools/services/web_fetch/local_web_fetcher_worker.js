const readline = require('node:readline');
const { fetchUrl } = require('./local_web_fetcher_core');

const rl = readline.createInterface({
  input: process.stdin,
  terminal: false,
});

function writeResponse(response) {
  process.stdout.write(`${JSON.stringify(response)}\n`);
}

async function handleLine(line) {
  let req;

  try {
    req = JSON.parse(line);
  } catch (_) {
    writeResponse({
      id: null,
      ok: false,
      error: 'invalid_json',
    });
    return;
  }

  try {
    const markdown = await fetchUrl(req.url);

    writeResponse({
      id: req.id,
      ok: true,
      markdown: markdown || '',
    });
  } catch (error) {
    process.stderr.write(`${error && error.stack ? error.stack : String(error)}\n`);

    writeResponse({
      id: req.id,
      ok: false,
      error: error && error.message ? error.message : String(error),
    });
  }
}

let queue = Promise.resolve();

rl.on('line', line => {
  queue = queue
    .then(() => handleLine(line))
    .catch(error => {
      process.stderr.write(`${error && error.stack ? error.stack : String(error)}\n`);
    });
});

rl.on('close', () => {
  process.exit(0);
});
