import os
import json
import uuid
import glob

    
def change_uuids():
    path = 'temp'
    files = []
    # r=root, d=directories, f = files
    for r, d, f in os.walk(path):
        for file in f:
            if '.json' in file and file != 'metadata.json':
                files.append(os.path.join(r, file))
    new_slice_ids = []
    # change uuids for report slice files
    for filename in files:
        new_uuid = str(uuid.uuid4())
        with open(filename, 'r') as f:
            data = json.load(f)
            data['report_slice_id'] = new_uuid # modify the report_slice_id
        new_name = '%s.json' % new_uuid
        new_file = os.path.join(os.getcwd()+'/temp/reports', new_name)
        with open(new_file, 'w') as f:
            json.dump(data, f, indent=4)
        new_slice_ids.append(new_uuid) # used for metadata
    
    # change uuid for metadata.json
    metadata = ''
    new_uuid = str(uuid.uuid4())
    for r, d, f in os.walk(path):
        for file in f:
            if file == 'metadata.json':
                metadata = os.path.join(r, file)
    with open(metadata, 'r') as f:
        data = json.load(f)
        data['report_id'] = new_uuid # modify the report_id
        for old_key, new_key in zip(data['report_slices'], new_slice_ids):
            data['report_slices'][new_key] = data['report_slices'][old_key]
            del data['report_slices'][old_key]
    with open('temp/reports/metadata.json', 'w') as f:
        json.dump(data, f, indent=4)

if __name__ == "__main__":
    change_uuids()