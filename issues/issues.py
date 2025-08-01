from github import Github
from github import Auth

# PyGithub-2.7.0

# Replace with your actual token and repository details
GITHUB_TOKEN = "<token here>"
REPO_OWNER = "Quantum-0"  # Or organization name
REPO_NAME = "twitch-arbuz-bot"

ISSUE_TITLE = "Automated Issue from Python"
ISSUE_BODY = "This issue was created programmatically using PyGithub."
ISSUE_LABELS = ["bug", "enhancement"]  # Optional: Add labels

try:
    # Authenticate with GitHub
    auth = Auth.Token(GITHUB_TOKEN)
    g = Github(auth=auth)

    # Get the repository
    repo = g.get_user().get_repo(REPO_NAME) # For user-owned repo
    # Or for organization-owned repo:
    # org = g.get_organization(REPO_OWNER)
    # repo = org.get_repo(REPO_NAME)

    # Create the issue
    # issue = repo.create_issue(title=ISSUE_TITLE, body=ISSUE_BODY, labels=ISSUE_LABELS)
    issues = repo.get_issues().get_page(0)
    print(issues)

    # print(f"Successfully created issue: {issue.html_url}")

except Exception as e:
    print(f"An error occurred: {e}")

# TODO: create issue - веб страничка
#  - выводим список проблем/предложений
#  - даём форму ввода bug report / feature request