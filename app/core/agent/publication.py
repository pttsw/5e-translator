"""Publish the tested bytes last; roll back replaced artifacts on write failure."""
import os
from pathlib import Path
import tempfile

from .commands import output_path


def publish(options, relative, staging):
    prepared, backups, replaced = {}, {}, []
    try:
        for suffix in ('.en', '.jobs', ''):
            target = output_path(options.output_root, relative + suffix, options.source_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            backups[target] = target.read_bytes() if target.exists() else None
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                prepared[target] = Path(handle.name)
                handle.write((Path(staging) / (relative + suffix)).read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
        for target, pending in prepared.items():
            os.replace(pending, target)
            replaced.append(target)
    except Exception:
        for target in reversed(replaced):
            if backups[target] is None:
                target.unlink(missing_ok=True)
            else:
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                    handle.write(backups[target])
                os.replace(handle.name, target)
        raise
    finally:
        for pending in prepared.values():
            pending.unlink(missing_ok=True)
    return str(options.output_root / relative)
