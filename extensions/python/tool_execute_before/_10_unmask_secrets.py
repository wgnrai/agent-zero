from helpers.extension import Extension
from helpers.secrets import get_secrets_manager


class UnmaskToolSecrets(Extension):

    async def execute(self, **kwargs):
        if not self.agent:
            return

        # Get tool args from kwargs
        tool_args = kwargs.get("tool_args")
        if not tool_args:
            return

        secrets_mgr = get_secrets_manager(self.agent.context)

        # Unmask placeholders in args for actual tool execution
        for k, v in tool_args.items():
            if isinstance(v, str):
                try:  # TEMP DIAGNOSTIC (secrets trace) - remove after dev-ticket-2026-09-12
                    import datetime as _dt
                    _m = secrets_mgr.load_secrets()
                    _fl = getattr(secrets_mgr, '_SecretsManager__files', None) or getattr(secrets_mgr, 'files', None) or list(getattr(type(secrets_mgr), '_instances', {}).keys())[:1]
                    _gh = _m.get('GH_TOKEN', '')
                    _cw = _m.get('CLOUDWAYS_API_KEY', '')
                    with open('/a0/usr/workdir/secrets-trace.log', 'a') as _f:
                        _f.write(f"{_dt.datetime.now().strftime('%H:%M:%S.%f')[:-3]} UNMASK k={k} pre_len={len(v)} mgr_id={id(secrets_mgr)} files={_fl} mapGH={'ABSENT' if 'GH_TOKEN' not in _m else len(_gh)} mapCW={'ABSENT' if 'CLOUDWAYS_API_KEY' not in _m else len(_cw)} mapGH_is_ph={_gh.startswith(chr(167) * 2 + 'secret(')}\n")
                except Exception:
                    pass
                tool_args[k] = secrets_mgr.replace_placeholders(v)
                try:  # TEMP DIAGNOSTIC (secrets trace)
                    _v2 = tool_args[k]
                    with open('/a0/usr/workdir/secrets-trace.log', 'a') as _f:
                        _f.write(f"{_dt.datetime.now().strftime('%H:%M:%S.%f')[:-3]} UNMASK-RESULT k={k} post_len={len(_v2)} changed={_v2 is not v}\n")
                except Exception:
                    pass
