"""AYON server queries for the Publish to AYON action. No Qt here."""


def ayon_connection(url, token):
    """A connection of its own: the host's global ayon_api connection (AYON in Nuke) is left alone."""
    import ayon_api
    connection = ayon_api.ServerAPI(url, token=token)
    if hasattr(connection, 'validate_token') and connection.validate_token() is False:
        raise PermissionError('The AYON server did not accept the login.')
    return connection


def folder_tree(connection, project):
    """Root folders, each {'name', 'label', 'path', 'type', 'children': [...], 'tasks': [...]}."""
    folders = list(connection.get_folders(project, fields={'id', 'name', 'label', 'path', 'folderType', 'parentId'}))
    tasks = list(connection.get_tasks(project, fields={'id', 'name', 'label', 'taskType', 'folderId'}))
    by_id = {str(f['id']): dict(f, children=[], tasks=[]) for f in folders}
    for task in tasks:
        folder = by_id.get(str(task.get('folderId')))
        if folder is not None:
            folder['tasks'].append(task)
    roots = []
    for folder in by_id.values():
        parent = by_id.get(str(folder.get('parentId'))) if folder.get('parentId') else None
        (parent['children'] if parent is not None else roots).append(folder)
    return roots
