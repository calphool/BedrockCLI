# BedrockCLI
This small collection of Python scripts basically creates functionality that's missing from the AWS CLI for Model enablement and model enablement status checking.  It uses Selenium/ChromeDriver to mimic missing functionality of the AWS CLI. 

---
## Usage:

### python bedrock_cli.py list-foundation-models-with-enablement-status

Will return the same output as the AWS CLI when given the list-foundation-models command, but enhances the output with an accessStatus node that tells you the current enablement status of this model.

### python bedrock_cli.py enable-foundation-model "Some model name"

Will walk through the enablement process for a given model.  Optional parameters (required for Anthropic models):

	--company-name
	--company-website-url
	--industry
	--internal-employees
	--external-users
	--use-case-description

## Notes:

The login code expects to find three parameters in environment variables: AWS_ACCOUNT_ID, IAM_ADMIN_USER, and IAM_ADMIN_PWD.  If they are not provided, the code will ask for them.  If you use this script as part of an automation, be sure to clear these environment variables immediately after invoking this python program.

Also, it's entirely likely that the login code will not work for your configuration.  Different organizations configure their sign-in process differently.  The code that was written was very basic, assuming the same login process as any user buying AWS services for the first time would expect, no SSO integration or anything like that.  You may have to modify the code if you are doing something more exotic.

Oh, also, the code automatically installs its own copy of ChromeDriver in order to function, and it does this based on whatever chrome version is installed on the machine that's invoking this code.  I have only tested it with Windows.  It's entirely possible it won't work quite right for Linux, though I don't know of anything specific that would cause it not to work.

**This code is offered as-is with no representation of suitability for your use case.  I will not be responsible for any damages you suffer as a result of the use of this code.  Use at your own risk.**