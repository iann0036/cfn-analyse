import pprint
import boto3
import json
import requests
import time
import cfnanalyse
import os
from urllib.parse import urlsplit

webhook = os.environ['SLACK_HOOK_URL']
website_bucket_prefix = os.environ['WEBSITE_BUCKET_PREFIX']

class PendingUpsert(Exception):
    pass

def send_message(api_gateway_id, key, execution_arn, task_token):
    parsed_url = urlsplit(webhook)
    host = parsed_url.netloc
    if host.endswith("aws"):
        print("Notifying via Chime... " + webhook)
        send_chime(api_gateway_id, key, execution_arn, task_token)
    else:
        print("Notifying via Slack... " + webhook)
        send_slack(api_gateway_id, key, execution_arn, task_token)

def send_chime(api_gateway_id, key, execution_arn, task_token):
    requests.post(webhook, data=json.dumps({
        "Content": "http://%s-ap-southeast-2.s3-website-ap-southeast-2.amazonaws.com/?gwid=%s&earn=%s&ttok=%s\n\nAnalysis of %s is complete.\n" % (website_bucket_prefix, api_gateway_id, execution_arn, task_token, key)
        }))

def send_slack(api_gateway_id, key, execution_arn, task_token):
    requests.post(webhook, data=json.dumps({
        "attachments": [
            {
                "fallback": "*Upsert of '%s' Denied - Manual Approval Required*" % (key),
                "color": "danger",
                "title": "Upsert of '%s' Denied - Manual Approval Required" % (key),
                "text": "<http://%s-ap-southeast-2.s3-website-ap-southeast-2.amazonaws.com/?gwid=%s&earn=%s&ttok=%s|View Details>" % (website_bucket_prefix, api_gateway_id, execution_arn, task_token),
                "fields": [
                    {
                        "title": "Status",
                        "value": "Pending Approval",
                        "short": False
                    }
                ],
                #"actions": [
                #    {
                #        "name": "approve",
                #        "text": "Approve",
                #        "style": "primary",
                #        "type": "button",
                #        "value": "approve"
                #    }
                #],
                "ts": int(time.time())
            }
        ]
    }))

def view(event):
    sfnclient = boto3.client('stepfunctions')
    s3resource = boto3.resource('s3')

    execution = sfnclient.describe_execution(
        executionArn=event['executionArn']
    )

    inputobj = json.loads(execution['input'])

    obj = s3resource.ObjectVersion(inputobj['bucket'], inputobj['key'], inputobj['version'])
    template = obj.get()['Body'].read().decode('utf-8')

    analysis = cfnanalyse.CfnAnalyse(template, inputobj['stack_name'])
    if analysis.process_resources():
        rule_evaluation_results = analysis.evaluate()
        return "{\"success\":true, \"requiresApproval\":\"%s\", \"title\":\"%s\", \"evaluation_results\":%s, \"resources\":%s, \"template\":%s, \"stack_name\":%s, \"description\":%s}" % (str(inputobj['requiresApproval']).lower(), inputobj['key'], json.dumps(rule_evaluation_results), json.dumps(analysis.resources), json.dumps(template), json.dumps(analysis.parameters['AWS::StackName']), json.dumps(analysis.description))
    pprint.pprint(analysis.resources)
    pprint.pprint(template)
    return "{\"success\":false, \"requiresApproval\":\"%s\", \"title\":\"%s\", \"resources\":%s, \"template\":%s, \"stack_name\":%s, \"description\":%s}" % (str(inputobj['requiresApproval']).lower(), inputobj['key'], json.dumps(analysis.resources), json.dumps(template), json.dumps(analysis.parameters['AWS::StackName']), json.dumps(analysis.description))

def upsert(event):
    print("Upserting %s" % (event['key']))

    cfnclient = boto3.client('cloudformation')

    tags = [
        {
            'Key': 'approvalRequired',
            'Value': 'false'
        }
    ]
    if event['requiresApproval']:
        tags = [
            {
                'Key': 'approvalRequired',
                'Value': 'true'
            },
            {
                'Key': 'approvedBy',
                'Value': event['approvedBy']
            },
        ]

    response = cfnclient.create_stack(
        StackName=event['stack_name'],
        TemplateURL='https://s3-ap-southeast-2.amazonaws.com/%s/%s?versionId=%s' % (event['bucket'], event['key'], event['version']),
        #Parameters=[
        #    {
        #        'ParameterKey': 'string',
        #        'ParameterValue': 'string',
        #        'UsePreviousValue': True|False,
        #        'ResolvedValue': 'string'
        #    },
        #],
        TimeoutInMinutes=120,
        Capabilities=[
            'CAPABILITY_NAMED_IAM',
        ],
        OnFailure='DELETE',
        Tags=tags
    )

    return {
        'bucket': event['bucket'],
        'key': event['key'],
        'version': event['version'],
        'requiresApproval': event['requiresApproval'],
        'approved': event['approved'],
        'approvedBy': event['approvedBy'],
        'stack_name': event['stack_name'],
        'action': 'upsert_wait',
        'stack_id': response['StackId']
    }

def upsert_wait(event):
    cfnclient = boto3.client('cloudformation')

    try:
        response = cfnclient.describe_stacks(
            StackName=event['stack_name']
        )

        if response['Stacks'][0]['StackStatus'] in ['CREATE_IN_PROGRESS','ROLLBACK_IN_PROGRESS','DELETE_IN_PROGRESS','UPDATE_IN_PROGRESS','UPDATE_COMPLETE_CLEANUP_IN_PROGRESS','UPDATE_ROLLBACK_IN_PROGRESS','UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS','REVIEW_IN_PROGRESS']:
            raise PendingUpsert(response['Stacks'][0]['StackStatus'])
    except:
        print("Exception whilst looking up stack status - assuming the stack has been deleted")

    return {
        'bucket': event['bucket'],
        'key': event['key'],
        'version': event['version'],
        'approved': event['approved'],
        'approvedBy': event['approvedBy'],
        'stack_name': event['stack_name'],
        'stack_id': event['stack_id'],
        'action': 'done',
        'requiresApproval': event['requiresApproval']
    }

def deny(event):
    sfnclient = boto3.client('stepfunctions')

    execution = sfnclient.describe_execution(
        executionArn=event['executionArn']
    )

    inputobj = json.loads(execution['input'])

    sfnclient.send_task_success(
        taskToken=event['taskToken'].replace("-","/"),
        output=json.dumps({
            'bucket': inputobj['bucket'],
            'key': inputobj['key'],
            'version': inputobj['version'],
            'approved': False,
            'deniedBy': 'X',
            'action': 'deny',
            'stack_name': inputobj['stack_name'],
            'requiresApproval': inputobj['requiresApproval']
        })
    )

    return "{\"success\":true}"

def approve(event):
    sfnclient = boto3.client('stepfunctions')

    execution = sfnclient.describe_execution(
        executionArn=event['executionArn']
    )

    inputobj = json.loads(execution['input'])

    sfnclient.send_task_success(
        taskToken=event['taskToken'].replace("-","/"),
        output=json.dumps({
            'bucket': inputobj['bucket'],
            'key': inputobj['key'],
            'version': inputobj['version'],
            'approved': True,
            'approvedBy': 'X',
            'action': 'upsert',
            'stack_name': inputobj['stack_name'],
            'requiresApproval': inputobj['requiresApproval']
        })
    )

    return "{\"success\":true}"

def start_execution(event):
    sfnclient = boto3.client('stepfunctions')
    apiclient = boto3.client('apigateway')
    s3client = boto3.client('s3')

    pprint.pprint(event)

    api_gateways = apiclient.get_rest_apis(
        limit=500
    ) # TODO: Deal with pagination
    for api_gateway in api_gateways['items']:
        if api_gateway['name'] == "CfnValidatorApiGateway": # TODO: Check for multiple deployments
            state_machines = sfnclient.list_state_machines() # TODO: Deal with pagination
            for state_machine in state_machines['stateMachines']:
                if state_machine['name'].startswith("CfnValidatorStateMachine-"): # TODO: Check for multiple deployments

                    default_stack_name = event['Records'][0]['s3']['object']['key'].rsplit(".", 1)[0]
                    stack_name = default_stack_name

                    # TODO: Preprocess here

                    requires_approval = True

                    s3tags = s3client.get_object_tagging(
                        Bucket=event['Records'][0]['s3']['bucket']['name'],
                        Key=event['Records'][0]['s3']['object']['key'],
                        VersionId=event['Records'][0]['s3']['object']['versionId']
                    )
                    
                    for tag in s3tags['TagSet']:
                        if tag['Key'].lower() == "stackname":
                            stack_name = tag['Value']
                    
                    if stack_name == default_stack_name:
                        s3obj = s3client.get_object(
                            Bucket=event['Records'][0]['s3']['bucket']['name'],
                            Key=event['Records'][0]['s3']['object']['key'],
                            VersionId=event['Records'][0]['s3']['object']['versionId']
                        )

                        if 'stackname' in s3obj['Metadata']:
                            stack_name = s3obj['Metadata']['stackname']

                    event_data = {
                        'action': 'process',
                        'bucket': event['Records'][0]['s3']['bucket']['name'],
                        'key': event['Records'][0]['s3']['object']['key'],
                        'version': event['Records'][0]['s3']['object']['versionId'],
                        'requiresApproval': requires_approval,
                        'stack_name': stack_name
                    }
                    execution = sfnclient.start_execution(
                        stateMachineArn=state_machine['stateMachineArn'],
                        input=json.dumps(event_data)
                    )

                    if requires_approval:
                        activities = sfnclient.list_activities()
                        for activity in activities['activities']:
                            if activity['name'] == "CfnValidatorHoldActivity":
                                task = sfnclient.get_activity_task(
                                    activityArn=activity['activityArn'],
                                    workerName='CfnValidator'
                                )
                                # TODO: Check here inputs are expected, potential race condition
                                send_message(
                                    api_gateway['id'],
                                    event['Records'][0]['s3']['object']['key'],
                                    execution['executionArn'],
                                    task['taskToken'].replace("/","-")
                                )
                    print("Begun processing %s" % (event['Records'][0]['s3']['object']['key']))

                    return event_data
    
    return { 
        'action': 'none',
        'requiresApproval': False
    }

def handler(event, context):
    if 'Records' in event: # TODO: Sanity check inputs
        return start_execution(event)
    elif 'action' in event:
        if event['action'] == "view":
            return view(event)
        elif event['action'] == "approve":
            return approve(event)
        elif event['action'] == "deny":
            return deny(event)
        elif event['action'] == "upsert":
            return upsert(event)
        elif event['action'] == "upsert_wait":
            return upsert_wait(event)

    return { 
        'action': 'none',
        'requiresApproval': False
    }
