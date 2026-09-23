#!/usr/bin/env python3
"""Offline gh/git/curl transport for the release workflow test; never contacts GitHub."""
import json
import os
from pathlib import Path
import subprocess
import sys

state_path = Path(os.environ["PUBLISHER_STATE"])
state = json.loads(state_path.read_text())
args = sys.argv[1:]
tool = Path(sys.argv[0]).name
state["calls"].append([tool, *args])


def save():
    state_path.write_text(json.dumps(state))


def release():
    asset_states = state.get("asset_states", {})
    return {
        "id": 42, "tag_name": state.get("release_tag", "v0.8.3"),
        "draft": state["draft"],
        "assets": [{"id": i + 1, "name": name,
                    "state": asset_states.get(name, "uploaded")}
                   for i, name in enumerate(state["assets"])],
    }


if tool == "git":
    if args[:2] == ["cat-file", "-t"]:
        print("tag")
    elif args[:1] == ["ls-remote"]:
        print("07d282b8d769d1de1729a26b16ef93ca8636fb70\trefs/tags/v0.8.3")
        print("eebbd2924740d70282604b161c66a697f7031780\trefs/tags/v0.8.3^{}")
    elif args[:1] == ["rev-parse"]:
        print("07d282b8d769d1de1729a26b16ef93ca8636fb70" if "^{tag}" in args[1]
              else "eebbd2924740d70282604b161c66a697f7031780")
    else:
        raise RuntimeError(args)
elif tool == "gh":
    if args[0] == "api":
        if state.get("api_error"):
            if state["api_error"] == "network":
                print("gh: connection refused", file=sys.stderr)
            else:
                print('{"message":"Bad credentials","status":"401"}')
                print("gh: HTTP 401", file=sys.stderr)
            save()
            sys.exit(1)
        if "/releases?" in args[-1]:
            if (state.get("empty_after_create") and state.get("created") and
                    not state.get("returned_empty_after_create")):
                state["returned_empty_after_create"] = True
                save()
                print("[]")
                sys.exit(0)
            if (state.get("empty_after_upload") and state.get("upload_count") and
                    not state.get("returned_empty_after_upload")):
                state["returned_empty_after_upload"] = True
                save()
                print("[]")
                sys.exit(0)
            pages = [[release()] if state["exists"] else []]
            if state.get("duplicate"):
                pages.append([release()])
            print(json.dumps(pages))
        else:
            raise RuntimeError(args)
    elif args[:2] == ["release", "create"]:
        assert not state["exists"]
        state.update(exists=True, created=True, draft=True)
    elif args[:2] == ["release", "upload"]:
        assert state["draft"] and state["exists"]
        name = Path(args[-1]).name
        assert name not in state["assets"]
        state["assets"][name] = Path(args[-1]).read_bytes().hex()
        state["upload_count"] = state.get("upload_count", 0) + 1
    elif args[:2] == ["release", "edit"]:
        assert state["draft"]
        state["draft"] = False
    else:
        raise RuntimeError(args)
elif tool == "curl":
    asset_id = int(args[args.index("-o") - 1].rsplit("/", 1)[-1])
    name = list(state["assets"])[asset_id - 1]
    Path(args[args.index("-o") + 1]).write_bytes(bytes.fromhex(state["assets"][name]))
elif tool == "find" and "-printf" in args:
    for p in sorted(Path(args[0]).rglob("*")):
        if p.is_file():
            print(p.name)
elif tool == "find":
    save()
    sys.exit(subprocess.call(["/usr/bin/find", *args]))
else:
    raise RuntimeError((tool, args))
save()
