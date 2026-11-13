import os
import sys

os.umask(0o077)
if sys.argv[1] == 'chatgpt':
    from litellm.llms.chatgpt.authenticator import Authenticator
elif sys.argv[1] == 'copilot':
    from litellm.llms.github_copilot.authenticator import Authenticator
else:
    raise SystemExit(2)

# The authenticator prints only the device sign-in instructions. Never print tokens.
Authenticator().get_access_token()
