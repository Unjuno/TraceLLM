"""Single-writer training engine with immutable snapshots and branch-aware replay."""
from dataclasses import asdict
import hashlib
from functools import wraps
import json
import math
import os
from pathlib import Path
import random
import threading
import time
from uuid import uuid4

from filelock import FileLock
import torch

from .data import BOS, EOS, collate, decode, encode, windows
from .model import Config, GPT, optimizer
from .store import Store

DAY = 86400
MAX_BYTES = 65536


class Conflict(Exception):
    pass


class Missing(Exception):
    pass


def uid():
    return uuid4().hex


def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()


def validate_text(title, body):
    if not isinstance(title, str) or not isinstance(body, str):
        raise ValueError('Text must be a string')
    if len(title) > 200 or len(body.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Title exceeds 200 characters or body exceeds 64 KiB')


def control_locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._control_lock:
            return method(self, *args, **kwargs)
    return wrapped


class Engine:
    def __init__(self, root: Path, config=None, max_steps=64):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lockfile = FileLock(str(self.root / 'engine.lock'))
        self.lockfile.acquire(timeout=0)
        self._control_lock = threading.RLock()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self.max_steps = max_steps
        try:
            self.checkpoint_dir = self.root / 'checkpoints'
            self.checkpoint_dir.mkdir(exist_ok=True, mode=0o700)
            self.store = Store(self.root / 'trace.sqlite3')
            with self.store.connect(True) as db:
                db.execute("UPDATE jobs SET status='failed', error='Interrupted before publication', "
                           "finished=? WHERE status='running'", (time.time(),))
            if not self.store.rows("SELECT value FROM meta WHERE key='current'"):
                config = config or Config()
                with torch.random.fork_rng(devices=[]):
                    torch.manual_seed(42)
                    model = GPT(config)
                    payload = {'format': 1, 'config': asdict(config),
                               'model': model.state_dict(), 'optimizer': optimizer(model).state_dict(),
                               'torch_rng': torch.get_rng_state(),
                               'sampler_rng': random.Random(42).getstate(),
                               'learned': {}, 'seen_hashes': [], 'updates': 0}
                    self._publish(payload, None, None, 'initial')
            payload = self.load_current()
            if config is not None and payload['config'] != asdict(config):
                raise ValueError('Existing model configuration differs; use a separate data directory')
            self.config = Config(**payload['config'])
            self.prune()
        except BaseException:
            self.lockfile.release()
            raise

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join()
        self.lockfile.release()

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._worker, name='trace-trainer', daemon=True)
        self._thread.start()

    def _worker(self):
        last_prune = 0
        while not self._stop.is_set():
            if time.time() - last_prune > 60:
                self.prune()
                last_prune = time.time()
            if not self.train_next():
                self._stop.wait(0.2)

    def _current(self):
        return self.store.rows("SELECT value FROM meta WHERE key='current'")[0]['value']

    def _load(self, checkpoint_id):
        rows = self.store.rows('SELECT * FROM checkpoints WHERE id=?', (checkpoint_id,))
        if not rows or not rows[0]['available']:
            raise Missing('Checkpoint unavailable')
        path = self.checkpoint_dir / (rows[0]['id'] + '.pt')
        if not path.is_file() or digest(path.read_bytes()) != rows[0]['digest']:
            raise RuntimeError('Checkpoint integrity check failed')
        payload = torch.load(path, map_location='cpu', weights_only=True)
        if payload.get('format') != 1:
            raise ValueError('Unsupported checkpoint format')
        return payload

    def load_current(self):
        with self._lock:
            return self._load(self._current())

    def _publish(self, payload, parent, previous, kind, job_id=None, metrics=None):
        checkpoint_id, now = uid(), time.time()
        final = self.checkpoint_dir / (checkpoint_id + '.pt')
        temporary = final.with_suffix('.tmp')
        try:
            with temporary.open('wb') as stream:
                torch.save(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, final)
            # Persist the directory rename on POSIX before publishing the pointer.
            if os.name == 'posix':
                descriptor = os.open(self.checkpoint_dir, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            sha = digest(final.read_bytes())
            with self.store.connect(True) as db:
                db.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?,1)',
                           (checkpoint_id, parent, previous, kind, now, now + 7*DAY, sha))
                if previous:
                    db.execute('UPDATE checkpoints SET expires=MAX(expires,?) WHERE id=?',
                               (now + 7*DAY, previous))
                db.execute("INSERT OR REPLACE INTO meta VALUES('current',?)", (checkpoint_id,))
                if job_id:
                    db.execute("UPDATE jobs SET status='succeeded', after_id=?, finished=?, "
                               "metrics=? WHERE id=?", (checkpoint_id, now,
                                                        json.dumps(metrics), job_id))
        finally:
            temporary.unlink(missing_ok=True)
        return self.store.rows('SELECT * FROM checkpoints WHERE id=?', (checkpoint_id,))[0]

    def create_page(self, title, body):
        validate_text(title, body)
        page_id, now = uid(), time.time()
        with self.store.connect(True) as db:
            db.execute('INSERT INTO pages VALUES(?,?,1,?,0)', (page_id, title, now))
            db.execute('INSERT INTO versions VALUES(?,?,1,?,?,?)',
                       (uid(), page_id, body, digest(body.encode()), now))
        return self.get_page(page_id)

    def get_page(self, page_id):
        rows = self.store.rows('SELECT p.*, v.id AS version_id, v.body, v.sha256 '
                               'FROM pages p JOIN versions v ON p.id=v.page_id '
                               'AND p.revision=v.revision WHERE p.id=? AND p.deleted=0', (page_id,))
        if not rows:
            raise Missing('Page not found')
        return rows[0]

    def pages(self):
        return self.store.rows('SELECT p.*, v.body, v.id AS version_id FROM pages p '
                               'JOIN versions v ON p.id=v.page_id AND p.revision=v.revision '
                               'WHERE deleted=0 ORDER BY updated DESC')

    def update_page(self, page_id, title, body, revision):
        validate_text(title, body)
        now = time.time()
        with self.store.connect(True) as db:
            row = db.execute('SELECT * FROM pages WHERE id=? AND deleted=0', (page_id,)).fetchone()
            if row is None:
                raise Missing('Page not found')
            if row['revision'] != revision:
                raise Conflict('Page changed elsewhere; reload before saving')
            current = db.execute('SELECT body FROM versions WHERE page_id=? AND revision=?',
                                 (page_id, revision)).fetchone()
            if row['title'] != title or current['body'] != body:
                db.execute('UPDATE pages SET title=?, revision=revision+1, updated=? WHERE id=?',
                           (title, now, page_id))
                db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?)',
                           (uid(), page_id, revision + 1, body, digest(body.encode()), now))
        return self.get_page(page_id)

    @control_locked
    def delete_page(self, page_id):
        with self.store.connect(True) as db:
            if not db.execute('UPDATE pages SET deleted=1 WHERE id=? AND deleted=0',
                              (page_id,)).rowcount:
                raise Missing('Page not found')
            db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE status='queued' "
                       'AND version_id IN (SELECT id FROM versions WHERE page_id=?)',
                       (time.time(), page_id))

    @control_locked
    def feed(self, page_id, revision, repeats, request_key):
        if not 1 <= repeats <= 10 or not 1 <= len(request_key) <= 128:
            raise ValueError('Invalid feed settings')
        signature = json.dumps([page_id, revision, repeats])
        with self.store.connect(True) as db:
            existing = db.execute('SELECT * FROM jobs WHERE request_key=?',
                                  (request_key,)).fetchone()
            if existing:
                if existing['signature'] != signature:
                    raise Conflict('Request key reused with different content')
                return dict(existing)
            version = db.execute('SELECT v.* FROM versions v JOIN pages p ON p.id=v.page_id '
                                 'WHERE p.id=? AND v.revision=? AND p.deleted=0',
                                 (page_id, revision)).fetchone()
            if version is None:
                raise Missing('Page revision not found')
            if not version['body'].strip():
                raise ValueError('Empty page cannot be trained')
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0] >= 64:
                raise Conflict('Training queue is full')
            job_id = uid()
            db.execute('INSERT INTO jobs(id,request_key,signature,version_id,repeats,status,created) '
                       "VALUES(?,?,?,?,?,'queued',?)",
                       (job_id, request_key, signature, version['id'], repeats, time.time()))
        return self.store.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]

    @control_locked
    def cancel(self, job_id):
        with self.store.connect(True) as db:
            if not db.execute("UPDATE jobs SET status='cancelled', finished=? "
                              "WHERE id=? AND status='queued'", (time.time(), job_id)).rowcount:
                raise Conflict('Only queued jobs can be cancelled')

    @staticmethod
    def _score(model, text):
        was_training = model.training
        model.eval()
        total, count = 0.0, 0
        with torch.no_grad():
            for example in windows(text, model.config.context):
                x, y = collate([example]); n = len(example[1])
                total += model.loss(x, y).item() * n
                count += n
        model.train(was_training)
        return total / count

    def train_next(self):
        with self._lock:
            with self.store.connect(True) as db:
                job = db.execute("SELECT j.*,v.body,v.page_id,v.sha256 FROM jobs j "
                                 "JOIN versions v ON v.id=j.version_id WHERE j.status='queued' "
                                 'ORDER BY j.created LIMIT 1').fetchone()
                if job is None:
                    return False
                job = dict(job)
                before = self._current()
                db.execute("UPDATE jobs SET status='running', started=?, before_id=? WHERE id=?",
                           (time.time(), before, job['id']))
            try:
                with torch.random.fork_rng(devices=[]):
                    payload = self._load(before)
                    model = GPT(Config(**payload['config']))
                    model.load_state_dict(payload['model'])
                    opt = optimizer(model); opt.load_state_dict(payload['optimizer'])
                    torch.set_rng_state(payload['torch_rng'])
                    rng = random.Random(); rng.setstate(payload['sampler_rng'])
                    old_versions = []
                    for pid, vid in payload['learned'].items():
                        if pid == job['page_id']:
                            continue
                        rows = self.store.rows('SELECT v.body,v.sha256 FROM versions v JOIN pages p '
                                               'ON p.id=v.page_id WHERE v.id=? AND p.deleted=0', (vid,))
                        old_versions.extend(rows)
                    before_loss = self._score(model, job['body'])
                    examples = windows(job['body'], model.config.context)
                    # Preserve exact page passes; shuffle within each pass, not replacement sampling.
                    schedule = []
                    for _ in range(job['repeats']):
                        cycle = examples.copy(); rng.shuffle(cycle); schedule.extend(cycle)
                    replay_budget = (len(job['body'].encode()) + 1) * job['repeats'] // 3
                    replay_remaining = replay_budget if old_versions else 0
                    new_targets = replay_targets = updates = 0
                    for new_example in schedule:
                        if updates >= self.max_steps or self._stop.is_set():
                            break
                        batch = [new_example]
                        added = 0
                        if replay_remaining:
                            text = rng.choice(old_versions)['body']
                            old = rng.choice(windows(text, model.config.context))
                            # 25% is a token budget across the job, not a page-count ratio.
                            added = min(len(old[1]), replay_remaining,
                                        max(1, math.ceil(len(new_example[1]) / 3)))
                            batch.append((old[0][:added], old[1][:added]))
                        x, y = collate(batch)
                        model.train(); opt.zero_grad(set_to_none=True)
                        loss = model.loss(x, y)
                        if not torch.isfinite(loss):
                            raise RuntimeError('Non-finite loss; update not published')
                        loss.backward()
                        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
                        opt.step()
                        if not all(torch.isfinite(p).all() for p in model.parameters()):
                            raise RuntimeError('Non-finite parameters; update not published')
                        new_targets += len(new_example[1]); replay_targets += added
                        replay_remaining -= added; updates += 1
                    if self._stop.is_set():
                        raise RuntimeError('Shutdown before publication; no update applied')
                    after_loss = self._score(model, job['body'])
                    if not math.isfinite(after_loss):
                        raise RuntimeError('Non-finite evaluation; update not published')
                    learned = dict(payload['learned']); learned[job['page_id']] = job['version_id']
                    metrics = {'loss_before': before_loss, 'loss_after': after_loss,
                               'loss_unit': 'nats/target-token (bytes and EOS)',
                               'new_targets': new_targets, 'replay_targets': replay_targets,
                               'requested_new_targets': (len(job['body'].encode())+1)*job['repeats'],
                               'updates': updates, 'capped': updates < len(schedule),
                               'quality': 'unverified', 'unique_page_bytes': len(job['body'].encode()),
                               'content_seen_before': job['sha256'] in payload.get('seen_hashes', [])}
                    seen = sorted(set(payload.get('seen_hashes', [])) | {job['sha256']})
                    payload.update(seen_hashes=seen, model=model.state_dict(), optimizer=opt.state_dict(),
                                   torch_rng=torch.get_rng_state(), sampler_rng=rng.getstate(),
                                   learned=learned, updates=payload['updates']+updates)
                    self._publish(payload, before, before, 'train', job['id'], metrics)
            except Exception as exc:
                with self.store.connect(True) as db:
                    db.execute("UPDATE jobs SET status='failed',finished=?,error=? WHERE id=? "
                               "AND status='running'", (time.time(), type(exc).__name__ +
                               ': training failed; previous checkpoint preserved', job['id']))
            return True

    def respond(self, page_id, revision, max_new_tokens=64, temperature=0.8, seed=42):
        if not 1 <= max_new_tokens <= 128 or not 0 <= temperature <= 2:
            raise ValueError('Invalid generation settings')
        p = self.get_page(page_id)
        if p['revision'] != revision:
            raise Conflict('Save/reload the page before generating')
        if not p['body'].strip():
            raise ValueError('Empty page')
        with self._lock, torch.random.fork_rng(devices=[]):
            cp = self._current(); payload = self._load(cp)
            model = GPT(Config(**payload['config'])); model.load_state_dict(payload['model'])
            model.eval(); torch.manual_seed(seed)
            prefix = encode(p['body'])[:-1]
            context = prefix[-model.config.context:]
            tokens = []
            with torch.no_grad():
                for _ in range(max_new_tokens):
                    logits = model(torch.tensor([context]))[0, -1].clone()
                    logits[BOS] = -float('inf')
                    token = (int(logits.argmax()) if temperature == 0 else
                             int(torch.multinomial((logits/temperature).softmax(-1), 1)))
                    tokens.append(token)
                    if token == EOS:
                        break
                    context = (context + [token])[-model.config.context:]
            meta = {'seed': seed, 'temperature': temperature, 'context_used': min(len(prefix),
                    model.config.context), 'context_truncated': len(prefix)>model.config.context,
                    'max_new_tokens': max_new_tokens, 'untrained': payload['updates'] == 0}
            response_id = uid()
            with self.store.connect(True) as db:
                db.execute('INSERT INTO responses VALUES(?,?,?,?,?,?,?)',
                           (response_id, p['version_id'], cp, time.time(), json.dumps(tokens),
                            decode(tokens), json.dumps(meta)))
        return next(r for r in self.responses(page_id) if r['id'] == response_id)

    def responses(self, page_id):
        self.get_page(page_id)
        rows = self.store.rows('SELECT r.*,v.revision FROM responses r JOIN versions v '
                               'ON v.id=r.version_id WHERE v.page_id=? '
                               'ORDER BY r.created DESC LIMIT 100', (page_id,))
        for row in rows:
            row['raw_tokens'] = json.loads(row['raw_tokens'])
            row['metadata'] = json.loads(row['metadata'])
        return rows

    def status(self):
        current = self._current()
        counts = self.store.rows("SELECT status,COUNT(*) AS count FROM jobs GROUP BY status")
        return {'checkpoint_id': current, 'config': asdict(self.config),
                'parameters': 12*self.config.layers*self.config.width**2 +
                (258+2*self.config.layers+1)*self.config.width,
                'jobs': {row['status']: row['count'] for row in counts},
                'retention_days': 7, 'device': 'cpu', 'precision': 'float32'}

    def history(self):
        jobs = self.store.rows('SELECT j.*,p.title,v.revision,v.page_id FROM jobs j '
                              'JOIN versions v ON j.version_id=v.id JOIN pages p ON p.id=v.page_id '
                              'ORDER BY j.created DESC LIMIT 200')
        for job in jobs:
            job['metrics'] = json.loads(job['metrics'])
        checkpoints = self.store.rows('SELECT * FROM checkpoints ORDER BY created DESC LIMIT 200')
        current = self._current()
        for cp in checkpoints:
            cp['current'] = cp['id'] == current
            cp['restorable'] = bool(cp['available'] and
                                    (cp['current'] or cp['expires'] > time.time()))
        return {'jobs': jobs, 'checkpoints': checkpoints, 'current': current}

    @control_locked
    def restore(self, checkpoint_id):
        with self._lock:
            # Queue mutation and restore are serialized across the publication below.
            rows = self.store.rows('SELECT * FROM checkpoints WHERE id=?', (checkpoint_id,))
            if not rows or not rows[0]['available']:
                raise Missing('Checkpoint unavailable')
            current = self._current()
            if rows[0]['expires'] <= time.time() and checkpoint_id != current:
                raise Missing('Checkpoint expired')
            if self.store.rows("SELECT id FROM jobs WHERE status IN ('queued','running')"):
                raise Conflict('Cancel queued jobs and wait for training before restoring')
            payload = self._load(checkpoint_id)
            return self._publish(payload, checkpoint_id, current, 'restore')

    def prune(self, now=None):
        now = time.time() if now is None else now
        with self._lock:
            current = self._current()
            with self.store.connect(True) as db:
                db.execute('UPDATE checkpoints SET available=0 WHERE expires<=? AND id<>?',
                           (now, current))
            # Also collect orphaned files left by a crash before database publication.
            available = {r['id'] for r in self.store.rows(
                'SELECT id FROM checkpoints WHERE available=1')}
            for path in self.checkpoint_dir.glob('*.pt'):
                if path.stem not in available:
                    path.unlink(missing_ok=True)
            for path in self.checkpoint_dir.glob('*.tmp'):
                path.unlink(missing_ok=True)
