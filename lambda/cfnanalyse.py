import json
import sys
import pprint
import ruamel.yaml
import ipaddr
import traceback
import base64
import requests
import time
import os

# TODO: Check for transforms

class CfnAnalyse(object):

    def __init__(self, cfn_template, stack_name):
        self.loadSpecification('latest')
        self.stack_name = stack_name

        def funcparse(loader, node):
            node.value = {
                ruamel.yaml.constructor.ScalarNode:   loader.construct_scalar,
                ruamel.yaml.constructor.SequenceNode: loader.construct_sequence,
                ruamel.yaml.constructor.MappingNode:  loader.construct_mapping,
            }[type(node)](node)
            node.tag = node.tag.replace(u'!Ref', 'Ref').replace(u'!', u'Fn::').replace(u'Fn::Fn::', u'Fn::')
            return dict([ (node.tag, node.value) ])

        funcnames = [ 'Ref', 'Base64', 'FindInMap', 'GetAtt', 'GetAZs', 'ImportValue', 'Join', 'Select', 'Split', 'Sub', 'And', 'Equals', 'If', 'Not', 'Or' ]

        for func in funcnames:
            ruamel.yaml.SafeLoader.add_constructor(u'!' + func, funcparse)

        self.cfn_template = ruamel.yaml.safe_load(cfn_template.replace("\t","    ")) # Does JSON too!

        # Init all the things
        with open("rules.yml", 'r') as file:
            self.rules = ruamel.yaml.safe_load(file)['rules']
        self.resetProperties()
        self.description = None
        if 'Description' in self.cfn_template:
            self.description = self.cfn_template['Description']

        if not 'Resources' in self.cfn_template:
            raise Exception("[FAIL] Not a valid template")

        print(str(len(self.cfn_template['Resources'])) + " resources")
    
    def resetProperties(self):
        self.parameters = {
            'AWS::AccountId': '123456789012',
            'AWS::NotificationARNs': ['arn:aws:sns:us-east-1:123456789012:MyTopic'],
            'AWS::NoValue': None,
            'AWS::Region': 'us-east-1',
            'AWS::StackId': 'arn:aws:cloudformation:us-east-1:123456789012:stack/MyStack/1c2fa620-982a-11e3-aff7-50e2416294e0',
            'AWS::StackName': self.stack_name,
            # Below not included in published defaults
            'AWS::Partition': 'aws',
            'AWS::URLSuffix': 'amazonaws.com'
        }
        self.resources = {}
        self.properties = []
        self.attributes = {}
        self.mappings = {}
        self.rule_evaluations = []
    
    def loadSpecification(self, version):
        available_versions = os.listdir('specifications/')
        available_versions.sort(key=lambda s: list(map(int, s.split('.')[:-1]))) # order lexically, [:-1] for the .json bit

        if version == 'latest':
            self.version = available_versions[-1]
        elif version == 'previous':
            index = available_versions.index(self.version)
            if index < 1:
                return False
            self.version = available_versions[index-1]
        
        with open("specifications/" + self.version, "r") as file:
            self.specification = json.loads(file.read())
        print("Loaded version: " + self.version)
        
        return True

    def validateRule(self, validator, rule, prop):
        value = prop['value']

        try:
            if isinstance(prop, list) and not isinstance(prop, str):
                for val in value:
                    if not self.validateRule(validator, rule, prop):
                        return False
                return True
            if validator == "stringEqual":
                if isinstance(rule, list) and not isinstance(rule, str):
                    return value in rule
                return rule == value
            elif validator == "stringNotEqual":
                if isinstance(rule, list) and not isinstance(rule, str):
                    return value not in rule
                return rule != value
            elif validator == "exists":
                return True
            elif validator == "notExists":
                return False
            elif validator == "cidrMatch" and rule == "rfc1918": # TODO: not doing IPv6
                return ((ipaddr.IPNetwork('192.168.0.0/16').overlaps(ipaddr.IPNetwork(value)) and ipaddr.IPNetwork(value).prefixlen >= 16) or (ipaddr.IPNetwork('172.16.0.0/12').overlaps(ipaddr.IPNetwork(value)) and ipaddr.IPNetwork(value).prefixlen >= 12) or (ipaddr.IPNetwork('10.0.0.0/8').overlaps(ipaddr.IPNetwork(value)) and ipaddr.IPNetwork(value).prefixlen >= 8))
            elif validator == "bool":
                if (bool(rule)):
                    return value in [True, "true", "True", "TRUE", 1]
                return value not in [True, "true", "True", "TRUE", 1]
            elif validator == "noImports":
                return value != "<i>Reference to an imported value</i>"
        except Exception as e:
            traceback.print_exc()
            print("RULE ERROR: " + str(e))
            return False

    def processResource(self, resource):
        self.properties = []
        self.attributes = {}

        if 'Attributes' in resource:
            for attrname, attr in resource['Attributes'].items():
                pass # TODO: Process resource attributes if necessary
        
        if 'Properties' in resource:
            for propname, prop in resource['Properties'].items():
                if resource['Type'] == "AWS::CloudFormation::CustomResource" or resource['Type'].startswith("Custom::"):
                    self.properties.append({
                        'name': resource['Type'] + "." + propname,
                        'type': 'String',
                        'value': self.resolvePropertyValue(prop, None)
                    })
                else:
                    self.processProperty(resource['Type'], propname, prop)

    def processProperty(self, resource_type, propname, prop):
        print("Begun processing property: " + propname)
        print("which is of resource type: " + resource_type)

        # TODO Special condition - deprecated functionality
        #if resource_type == "AWS::ElasticBeanstalk::Application" and propname == "ApplicationVersions":
        #    return
        
        # TODO Special condition - deprecated functionality
        #if resource_type == "AWS::ElasticBeanstalk::Application" and propname == "ConfigurationTemplates":
        #    return

        spec_types = self.specification['ResourceTypes'].copy()
        spec_types.update(self.specification['PropertyTypes'])

        # Explanation on this: if resource.propname exists in the specification, it is handled as a distinct resource (e.g. 1.5.0/AWS::AutoScaling::AutoScalingGroup.NotificationConfiguration)
        if resource_type + "." + propname in spec_types:
            for subpropname, subprop in prop.items():
                self.processProperty(resource_type + "." + propname, subpropname, subprop)
            return
        else:
            propdef = spec_types[resource_type]['Properties'][propname]

        if 'PrimitiveType' in propdef:
            if propdef['PrimitiveType'] == "Json": # can JSON have sub refs? YES! Yes it can
                self.properties.append({
                    'name': resource_type + "." + propname,
                    'type': propdef['PrimitiveType'],
                    'value': self.resolvePropertyValue(prop, 'Map', True)
                })
            else:
                self.properties.append({
                    'name': resource_type + "." + propname,
                    'type': propdef['PrimitiveType'],
                    'value': self.resolvePropertyValue(prop, propdef['PrimitiveType'])
                })
        elif propdef['Type'] == "List" or propdef['Type'] == "Map":
            if 'PrimitiveItemType' in propdef:
                if isinstance(prop, dict):
                    print("Resolving List/Map with PrimitiveItemType")
                    self.properties.append({
                        'name': resource_type + "." + propname + "{}",
                        'type': propdef['PrimitiveItemType'],
                        'value': self.resolvePropertyValue(prop, propdef['Type'], True)
                    })
                elif isinstance(prop, list) and not isinstance(prop, str):
                    for proplistitem in prop:
                        self.properties.append({
                            'name': resource_type + "." + propname + "[]",
                            'type': propdef['PrimitiveItemType'],
                            'value': self.resolvePropertyValue(proplistitem, propdef['PrimitiveItemType'])
                        })
                else: # Weird case: an item can be defined as a List, but only provide a string, and thats still valid -_-
                    self.properties.append({
                        'name': resource_type + "." + propname + "[]",
                        'type': propdef['PrimitiveItemType'],
                        'value': self.resolvePropertyValue(prop, propdef['PrimitiveItemType'])
                    })

            elif 'ItemType' in propdef:
                for listitem in prop:
                    if isinstance(listitem, str):
                        return #TODO: listitem = resolvePropertyValue(prop, "List") --- need to process a reference instead of a list here
                    else:
                        for subpropname, subprop in listitem.items():
                            if propdef['ItemType'] == "Tag": # TODO: eww
                                self.processProperty(propdef['ItemType'], subpropname, subprop)
                            else:
                                self.processProperty(resource_type.split(".")[0] + "." + propdef['ItemType'], subpropname, subprop)
            else:
                raise Exception('Property has no found ItemType')
        elif (resource_type.split(".")[0] + "." + propdef['Type']) in self.specification['PropertyTypes']:
            for subpropname, subprop in prop.items():
                self.processProperty(resource_type.split(".")[0] + "." + propdef['Type'], subpropname, subprop)
        else:
            print(resource_type.split(".")[0] + "." + propdef['Type'])
            pprint.pprint(propdef)
            raise Exception('Unhandled Property Type')

    def resolvePropertyValue(self, prop, expected_type, accept_map = False):
        if isinstance(prop, dict):
            if 'Ref' in prop:
                if prop['Ref'] in self.parameters.keys():
                    return self.parameters[self.resolvePropertyValue(prop['Ref'], expected_type)]
                else:
                    raise Exception('Unable to process property reduce - no ref')
            elif 'Fn::Base64' in prop:
                return str(base64.b64encode(self.resolvePropertyValue(prop['Fn::Base64'], "String").encode()))
            elif 'Fn::FindInMap' in prop:        
                return self.mappings[self.resolvePropertyValue(prop['Fn::FindInMap'][0], "String")][self.resolvePropertyValue(prop['Fn::FindInMap'][1], "String")][self.resolvePropertyValue(prop['Fn::FindInMap'][2], "String")]
            elif 'Fn::GetAZs' in prop:
                return ["us-east-1a", "us-east-1b", "us-east-1c"] # TODO: get from account
            elif 'Fn::Join' in prop:
                return self.resolvePropertyValue(prop['Fn::Join'][0], "String").join(self.resolvePropertyValue(prop['Fn::Join'][1], "List")) # TODO: check
            elif 'Fn::Split' in prop:
                return self.resolvePropertyValue(prop['Fn::Split'][1], "List").split(self.esolvePropertyValue(prop['Fn::Split'][0], "String")) # TODO: check
            elif 'Fn::Select' in prop:
                return self.resolvePropertyValue(prop['Fn::Select'][1], "List")[int(str(self.resolvePropertyValue(prop['Fn::Select'][0], "Integer")))] # TODO: check
            elif 'Fn::GetAtt' in prop:
                return '*unknown_getattr*' # TODO: gave up
                if self.resolvePropertyValue(prop['Fn::GetAtt'][0], "String") in resources['attributes']:
                    if self.resolvePropertyValue(prop['Fn::GetAtt'][1], "String") in resources[self.resolvePropertyValue(prop['Fn::GetAtt'][0], "String")]['attributes']:
                        return resources[self.resolvePropertyValue(prop['Fn::GetAtt'][0], "String")]['attributes'][self.resolvePropertyValue(prop['Fn::GetAtt'][1], "String")]
            elif 'Fn::If' in prop:
                if self.resolvePropertyValue(prop['Fn::If'][0], "Boolean") in [True, "true", "True", "TRUE", 1]:
                    return self.resolvePropertyValue(prop['Fn::If'][1], expected_type)
                else:
                    return self.resolvePropertyValue(prop['Fn::If'][2], expected_type)
            elif 'Fn::And' in prop:
                for subprop in prop['Fn::And']:
                    if self.resolvePropertyValue(subprop, "Boolean") not in [True, "true", "True", "TRUE", 1]:
                        return False
                return True
            elif 'Fn::Equals' in prop:
                return self.resolvePropertyValue(prop['Fn::Equals'][0], expected_type) == self.resolvePropertyValue(prop['Fn::Equals'][1], expected_type)
            elif 'Fn::Not' in prop:
                return self.resolvePropertyValue(prop['Fn::Not'][0], expected_type) == False
            elif 'Fn::Or' in prop:
                for subprop in prop['Fn::Or']:
                    if self.resolvePropertyValue(subprop, "Boolean") in [True, "true", "True", "TRUE", 1]:
                        return True
                return False
            elif 'Fn::ImportValue' in prop:
                return "*unknown_imported_value*"
            elif 'Fn::Sub' in prop:
                resolved_args = self.resolvePropertyValue(prop['Fn::Sub'], expected_type, True)
                sub_var_map = {}
                if isinstance(resolved_args, list) and not isinstance(resolved_args, str):
                    src_str = self.resolvePropertyValue(resolved_args[0], "String")
                    sub_var_map = self.resolvePropertyValue(resolved_args[1], "Map", True)
                else:
                    src_str = self.resolvePropertyValue(resolved_args, "String")
                
                # Resolve substitutions
                index = src_str.find("${")
                resolvable = True
                max_loops = 20
                while (index > -1 or not resolvable) and max_loops > 0:
                    max_loops-=1
                    endindex = src_str.find("}", index)
                    if endindex < 0:
                        print("Left open bracket without right when resolving a Fn::Sub")
                        resolvable = False
                    varname = src_str[index+2:endindex]
                    if varname[0] == '!': # Literal
                        index = -1
                        src_str = src_str.replace("${" + varname + "}", varname[1:], 1)
                        break
                    elif varname in sub_var_map:
                        src_str = src_str.replace("${" + varname + "}", self.resolvePropertyValue(sub_var_map[varname], "String"), 1)
                        print("Replaced ${" + varname + "} with custom mapping: " + self.resolvePropertyValue(sub_var_map[varname], "String"))
                    elif varname in self.parameters:
                        src_str = src_str.replace("${" + varname + "}", self.parameters[varname], 1)
                        print("Replaced ${" + varname + "} with parameter: " + self.parameters[varname])
                    else:
                        resolvable = False
                        print(varname + " is not resolvable")
                    index = src_str.find("${")
                if index == -1:
                    return src_str
                
                print("Failed to resolve a Fn::Sub")
            elif accept_map:
                # it's a map
                resolved_map = {}
                for k, v in prop.items():
                    resolved_map[k] = self.resolvePropertyValue(v, "String", True)
                return resolved_map
            raise Exception('Unable to process property reduce')
        if isinstance(prop, list) and not isinstance(prop, str):
            print("Iterating through list or props to resolve")
            resolvedlist = []
            for listitem in prop:
                resolvedlist.append(self.resolvePropertyValue(listitem, "String", True))
            return resolvedlist
        else:
            # TODO: double check expected_type matches here
            return prop
        raise Exception('Unable to evaluate property function')

    def process_resources(self, iterate_all_versions = True):
        if 'Mappings' in self.cfn_template:
            self.mappings = self.cfn_template['Mappings']

        if 'Parameters' in self.cfn_template:
            for name, parameter in self.cfn_template['Parameters'].items():
                # TODO: assume we are not accepting non-default params
                if 'Default' in parameter:
                    self.parameters[name] = parameter['Default']
                else:
                    self.parameters[name] = "<i>Value for the parameter " + name + "</i>" # TODO: replace placeholder

        processedAFullResource = True
        cfnresources = self.cfn_template['Resources']
        while processedAFullResource:
            processedAFullResource = False
            for name, resource in dict(cfnresources).items(): # dict() to clone for loop
                try:
                    self.processResource(resource)
                    self.resources[name] = {
                        'properties': self.properties,
                        'attributes': self.attributes,
                        'type': resource['Type']
                    }
                    self.parameters[name] = '<i>Reference to ' + name + "</i>" # TODO: Make this better
                    processedAFullResource = True
                    del cfnresources[name]
                    print("** Processed " + name + " [" + resource['Type'] + "] **")
                except Exception as e:
                    traceback.print_exc()
                    print("PROCESSING ERROR: " + str(e))
                    continue

        if len(cfnresources) > 0:
            if self.loadSpecification('previous'):
                self.resetProperties()
                return self.process_resources(True)

            print("\n[ERROR] Could not process all items\n")
            pprint.pprint(cfnresources)
            print("")
            return False
        
        return True

    def evaluate(self): # TODO: This code can be compacted
        print("Begun Evaluating...")

        self.rule_evaluations = []

        for resource, attributes in self.resources.items():
            for rule in self.rules:
                if 'level' not in rule:
                    rule['level'] = "error"
                if 'rule' not in rule:
                    rule['rule'] = ""
                if rule['validator'] == "resourceNotExists": # TODO: no pass for this
                    if attributes['type'] != rule['resource']:
                        print("[PASS] '" + rule['desc'] + "' matched against '" + resource + "'")
                        self.rule_evaluations.append({
                            'result': 'PASS',
                            'description': rule['desc'],
                            'resource': resource
                        })
                    elif rule['level'] == 'warn':
                        print("[WARN] '" + rule['desc'] + "' matched against '" + resource + "'")
                        self.rule_evaluations.append({
                            'result': 'WARN',
                            'description': rule['desc'],
                            'resource': resource
                        })
                    else:
                        print("[FAIL] '" + rule['desc'] + "' matched against '" + resource + "'")
                        self.rule_evaluations.append({
                            'result': 'FAIL',
                            'description': rule['desc'],
                            'resource': resource
                        })
                    continue
                elif rule['validator'] == "tagsMustExist":
                    if attributes['type'] == rule['resource'] or rule['resource'] == "*":
                        tags_not_found = True
                        for tag in rule['rule']:
                            tags_not_found = True
                            for prop in attributes['properties']:
                                if prop['name'] == 'Tag.Key' and prop['value'] == tag:
                                    tags_not_found = False
                            if tags_not_found:
                                break
                        if tags_not_found:
                            if rule['level'] == 'warn':
                                print("[WARN] '" + rule['desc'] + "' matched against '" + resource + "'")
                                self.rule_evaluations.append({
                                    'result': 'WARN',
                                    'description': rule['desc'],
                                    'resource': resource
                                })
                            else:
                                print("[FAIL] '" + rule['desc'] + "' matched against '" + resource + "'")
                                self.rule_evaluations.append({
                                    'result': 'FAIL',
                                    'description': rule['desc'],
                                    'resource': resource
                                })
                        else:
                            print("[PASS] '" + rule['desc'] + "' matched against '" + resource + "'")
                            self.rule_evaluations.append({
                                'result': 'PASS',
                                'description': rule['desc'],
                                'resource': resource
                            })
                    continue
                elif rule['validator'] == "portEquals":
                    if attributes['type'] == rule['resource']:
                        success = False
                        if attributes['type'] == rule['resource']:
                            for prop in attributes['properties']:
                                if prop['name'] == (rule['resource'] + ".SecurityGroupIngress.FromPort") and prop['value'] in rule['rule']:
                                    for prop_b in attributes['properties']:
                                        if prop_b['name'] == (rule['resource'] + ".SecurityGroupIngress.ToPort") and prop_b['value'] == prop['value']:
                                            print("[PASS] '" + rule['desc'] + "' matched against '" + resource + "'")
                                            success = True
                                            self.rule_evaluations.append({
                                                'result': 'PASS',
                                                'description': rule['desc'],
                                                'resource': resource
                                            })
                        if success:
                            continue
                        if rule['level'] == 'warn':
                            print("[WARN] '" + rule['desc'] + "' matched against '" + resource + "'")
                            self.rule_evaluations.append({
                                'result': 'WARN',
                                'description': rule['desc'],
                                'resource': resource
                            })
                        else:
                            print("[FAIL] '" + rule['desc'] + "' matched against '" + resource + "'")
                            self.rule_evaluations.append({
                                'result': 'FAIL',
                                'description': rule['desc'],
                                'resource': resource
                            })
                    continue
                for prop in attributes['properties']:
                    if prop['name'] == (rule['resource'] + "." + rule['property']) or rule['resource'] == "*" or (rule['property'] == "*" and prop['name'].startswith(rule['resource'] + ".")):
                        if self.validateRule(rule['validator'], rule['rule'], prop):
                            print("[PASS] '" + rule['desc'] + "' matched against '" + resource + " (" + prop['name'] + ")'")
                            self.rule_evaluations.append({
                                'result': 'PASS',
                                'description': rule['desc'],
                                'resource': resource,
                                'property': prop['name']
                            })
                        elif rule['level'] == 'warn':
                            print("[WARN] '" + rule['desc'] + "' matched against '" + resource + " (" + prop['name'] + ")'")
                            self.rule_evaluations.append({
                                'result': 'WARN',
                                'description': rule['desc'],
                                'resource': resource,
                                'property': prop['name']
                            })
                        else:
                            print("[FAIL] '" + rule['desc'] + "' matched against '" + resource + " (" + prop['name'] + ")'")
                            self.rule_evaluations.append({
                                'result': 'FAIL',
                                'description': rule['desc'],
                                'resource': resource,
                                'property': prop['name']
                            })
        
        return self.rule_evaluations

