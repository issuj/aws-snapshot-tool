#!/usr/bin/env python
#
# (c) 2012/2014 E.M. van Nuil / Oblivion b.v.
#
# makesnapshots.py version 3.3
#
# Changelog
# version 1:   Initial version
# version 1.1: Added description and region
# version 1.2: Added extra error handeling and logging
# version 1.3: Added SNS email functionality for succes and error reporting
# version 1.3.1: Fixed the SNS and IAM problem
# version 1.4: Moved all settings to config file
# version 1.5: Select volumes for snapshotting depending on Tag and not from config file
# version 1.5.1: Added proxyHost and proxyPort to config and connect
# version 1.6: Public release
# version 2.0: Added daily, weekly and montly retention
# version 3.0: Rewrote deleting functions, changed description
# version 3.1: Fix a bug with the deletelist and added a pause in the volume loop
# version 3.2: Tags of the volume are placed on the new snapshot
# version 3.3: Merged IAM role addidtion from Github

from boto.ec2.connection import EC2Connection
from boto.ec2.regioninfo import RegionInfo
import boto.sns
from datetime import datetime
import time
import sys
import logging
from config import config


if (len(sys.argv) < 2):
    print('Please add a positional argument: day, week or month.')
    quit()
else:
    if sys.argv[1] == 'day':
        period = 'day'
        date_suffix = datetime.today().strftime('%a')
    elif sys.argv[1] == 'week':
        period = 'week'
        date_suffix = datetime.today().strftime('%U')
    elif sys.argv[1] == 'month':
        period = 'month'
        date_suffix = datetime.today().strftime('%b')
    else:
        print('Please use the parameter day, week or month')
        quit()

# Message to return result via SNS
message = ""
errmsg = ""

# Counters
total_creates = 0
total_deletes = 0
count_errors = 0

# List with snapshots to delete
deletelist = []

# Setup logging
logging.basicConfig(filename=config['log_file'], level=logging.INFO)
start_message = 'Started taking %(period)s snapshots at %(date)s' % {
    'period': period,
    'date': datetime.today().strftime('%d-%m-%Y %H:%M:%S')
}
message += start_message + "\n\n"
logging.info(start_message)

# Get settings from config.py
aws_access_key = config['aws_access_key']
aws_secret_key = config['aws_secret_key']
ec2_region_name = config['ec2_region_name']
ec2_region_endpoint = config['ec2_region_endpoint']
sns_arn = config.get('arn')
proxyHost = config.get('proxyHost')
proxyPort = config.get('proxyPort')

region = RegionInfo(name=ec2_region_name, endpoint=ec2_region_endpoint)

# Number of snapshots to keep
keep_week = config['keep_week']
keep_day = config['keep_day']
keep_month = config['keep_month']
count_success = 0
count_total = 0

# Connect to AWS using the credentials provided above or in Environment vars or using IAM role.
print 'Connecting to AWS'
if proxyHost:
    # proxy:
    # using roles
    if aws_access_key:
        conn = EC2Connection(aws_access_key, aws_secret_key, region=region, proxy=proxyHost, proxy_port=proxyPort)
    else:
        conn = EC2Connection(region=region, proxy=proxyHost, proxy_port=proxyPort)
else:
    # non proxy:
    # using roles
    if aws_access_key:
        conn = EC2Connection(aws_access_key, aws_secret_key, region=region)
    else:
        conn = EC2Connection(region=region)

# Connect to SNS
if sns_arn:
    print 'Connecting to SNS'
    if proxyHost:
        # proxy:
        # using roles:
        if aws_access_key:
            sns = boto.sns.connect_to_region(ec2_region_name, aws_access_key_id=aws_access_key, aws_secret_access_key=aws_secret_key, proxy=proxyHost, proxy_port=proxyPort)
        else:
            sns = boto.sns.connect_to_region(ec2_region_name, proxy=proxyHost, proxy_port=proxyPort)
    else:
        # non proxy:
        # using roles
        if aws_access_key:
            sns = boto.sns.connect_to_region(ec2_region_name, aws_access_key_id=aws_access_key, aws_secret_access_key=aws_secret_key)
        else:
            sns = boto.sns.connect_to_region(ec2_region_name)

def get_resource_tags(resource_id):
    resource_tags = {}
    if resource_id:
        tags = conn.get_all_tags({ 'resource-id': resource_id })
        for tag in tags:
            # Tags starting with 'aws:' are reserved for internal use
            if not tag.name.startswith('aws:'):
                resource_tags[tag.name] = tag.value
    return resource_tags

def set_resource_tags(resource, tags):
    for tag_key, tag_value in tags.iteritems():
        if tag_key not in resource.tags or resource.tags[tag_key] != tag_value:
            print 'Tagging %(resource_id)s with [%(tag_key)s: %(tag_value)s]' % {
                'resource_id': resource.id,
                'tag_key': tag_key,
                'tag_value': tag_value
            }
            resource.add_tag(tag_key, tag_value)

# Get all the instances that match the tag criteria
print 'Finding volumes that match the requested tag ({ "tag:%(tag_name)s": "%(tag_value)s" })' % config
insts = conn.get_only_instances(filters={ 'tag:' + config['tag_name']: config['tag_value'] })

# Iterate through each instance in the list
for i in insts:
    # Get all the volumes attached to this instance
    volumes = conn.get_all_volumes(filters={'attachment.instance-id': i.id})
    if 'Name' in i.tags:
        instName= "%s" % i.tags['Name']
    else:
        instName= "%s" % i.id
    # Iterate through each volume attached to the selected instances
    for vol in volumes:
        try:
            count_total += 1
            logging.info(vol)
            tags_volume = get_resource_tags(vol.id)
            # Detailed info for 'description' tag
            description = 'BACKUP:%(instName)s %(period)s_snapshot %(vol_id)s_%(period)s_%(date_suffix)s by snapshot script at %(date)s' % {
                'instName': instName,
                'period': period,
                'vol_id': vol.id,
                'date_suffix': date_suffix,
                'date': datetime.today().strftime('%d-%m-%Y %H:%M:%S')
            }
            try:
                # Create snapshot
                current_snap = vol.create_snapshot(description)
                # Give snapshot the same tags from volume
                set_resource_tags(current_snap, tags_volume)
                # Uses instance name for snapshot name
                set_resource_tags(current_snap, {"Name":instName})
                suc_message = 'Snapshot created with description: %s and tags: %s' % (description, str(tags_volume))
                print '     ' + suc_message
                logging.info(suc_message)
                total_creates += 1
            except Exception, e:
                print "Unexpected error:", sys.exc_info()[0]
                logging.error(e)
                pass
    
            snapshots = vol.snapshots()
            deletelist = []
            for snap in snapshots:
                sndesc = snap.description
                if (sndesc.find('week_snapshot') >= 0 and period == 'week'):
                    deletelist.append(snap)
                elif (sndesc.find('day_snapshot') >= 0 and period == 'day'):
                    deletelist.append(snap)
                elif (sndesc.find('month_snapshot') >= 0 and period == 'month'):
                    deletelist.append(snap)
                else:
                    logging.info('     Skipping, not added to deletelist: ' + sndesc)
    
            for snap in deletelist:
                logging.info(snap)
                logging.info(snap.start_time)
    
            def date_compare(snap1, snap2):
                if snap1.start_time < snap2.start_time:
                    return -1
                elif snap1.start_time == snap2.start_time:
                    return 0
                return 1
    
            deletelist.sort(date_compare)
            if period == 'day':
                keep = keep_day
            elif period == 'week':
                keep = keep_week
            elif period == 'month':
                keep = keep_month
            delta = len(deletelist) - keep
            for i in range(delta):
                del_message = '     Deleting snapshot ' + deletelist[i].description
                logging.info(del_message)
                deletelist[i].delete()
                total_deletes += 1
            time.sleep(3)
        except:
            print "Unexpected error:", sys.exc_info()[0]
            logging.error('Error in processing volume with id: ' + vol.id)
            errmsg += 'Error in processing volume with id: ' + vol.id
            count_errors += 1
        else:
            count_success += 1

# Result message
result = '\nFinished making snapshots at %(date)s with %(count_success)s snapshots of %(count_total)s possible.\n\n' % {
    'date': datetime.today().strftime('%d-%m-%Y %H:%M:%S'),
    'count_success': count_success,
    'count_total': count_total
}

message += result
message += "\nTotal snapshots created: " + str(total_creates)
message += "\nTotal snapshots errors: " + str(count_errors)
message += "\nTotal snapshots deleted: " + str(total_deletes) + "\n"

print '\n' + message + '\n'
print result

# SNS reporting
if sns_arn:
    if errmsg:
        sns.publish(sns_arn, 'Error in processing volumes: ' + errmsg, 'Error with AWS Snapshot')
    sns.publish(sns_arn, message, 'Finished AWS snapshotting')

logging.info(result)

