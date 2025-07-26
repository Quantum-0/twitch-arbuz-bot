def extract_targets(text: str, streamer_name: str) -> list[str]:
    # m = re.match("!\\w+ @.*[ $]", text)
    # if m:
    #     return m.lastgroup
    # else:
    #     return None
    command, *other = text.split()
    other = [
        o for o
        in other
        if
        (o.startswith("@") and len(o) > 1)
        or
        o.lower() in [
            "все", "всех", "all", "всем", "кого-то", "стримера", "стримлера", "стример", "стримлер", "чат", "чаттерсы", "чаттерсов", "чач"
        ]
    ]

    result = []
    for o in other:
        if o in {"стримера", "стримлера", "стример", "стримлер"}:
            o = "@" + streamer_name
        if o not in result:
            result.append(o)

    return result

def join_targets(targets) -> str | None:
    if not targets:
        return None
    elif len(targets) == 1:
        return targets[0]
    else:
        return ", ".join(targets[:-1]) + " и " + targets[-1]