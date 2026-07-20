def paginate(method, key, **kwargs):
    """Paginate a Slack API method, yielding items from the given response key."""
    cursor = None
    while True:
        resp = method(cursor=cursor, **kwargs)
        yield from resp[key]
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

def get_team_id(client):
    """Return the workspace team_id for API calls that require it (Enterprise Grid)."""
    teams = client.auth_teams_list().get("teams", [])
    return teams[0]["id"] if teams else None
