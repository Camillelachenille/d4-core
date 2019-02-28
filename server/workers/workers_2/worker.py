#!/usr/bin/env python3

import os
import sys
import time
import json
import redis

import datetime

host_redis_stream = "localhost"
port_redis_stream = 6379

redis_server_stream = redis.StrictRedis(
                    host=host_redis_stream,
                    port=port_redis_stream,
                    db=0)

host_redis_metadata = "localhost"
port_redis_metadata = 6380

redis_server_metadata = redis.StrictRedis(
                    host=host_redis_metadata,
                    port=port_redis_metadata,
                    db=0)

type_meta_header = 2
type_defined = 254
max_buffer_length = 100000

save_to_file = True

def get_save_dir(dir_data_uuid, year, month, day):
    dir_path = os.path.join(dir_data_uuid, year, month, day)
    if not os.path.isdir(dir_path):
        os.makedirs(dir_path)
    return dir_path

def check_json_file(json_file):
    # the json object must contain a type field
    if "type" in json_file:
        return True
    else:
        return False

def on_error(session_uuid, type_error, message):
    redis_server_stream.sadd('Error:IncorrectType', session_uuid)
    redis_server_metadata.hset('metadata_uuid:{}'.format(uuid), 'Error', 'Error: Type={}, {}'.format(type_error, message))
    clean_db(session_uuid)
    print('Incorrect format')
    sys.exit(1)

def clean_db(session_uuid):
    clean_stream(stream_meta_json, type_meta_header, session_uuid)
    clean_stream(stream_defined, type_defined, session_uuid)
    redis_server_stream.srem('ended_session', session_uuid)
    redis_server_stream.srem('working_session_uuid:{}'.format(type_meta_header), session_uuid)

def clean_stream(stream_name, type, session_uuid):
    redis_server_stream.srem('session_uuid:{}'.format(type), session_uuid)
    redis_server_stream.hdel('map-type:session_uuid-uuid:{}'.format(type), session_uuid)
    redis_server_stream.delete(stream_name)

if __name__ == "__main__":

    if len(sys.argv) != 2:
        print('usage:', 'Worker.py', 'session_uuid')
        exit(1)

    session_uuid = sys.argv[1]
    stream_meta_json = 'stream:{}:{}'.format(type_meta_header, session_uuid)
    stream_defined = 'stream:{}:{}'.format(type_defined, session_uuid)

    id = '0'
    buffer = b''

    stream_name = stream_meta_json
    type = type_meta_header

    # track launched worker
    redis_server_stream.sadd('working_session_uuid:{}'.format(type_meta_header), session_uuid)

    # get uuid
    res = redis_server_stream.xread({stream_name: id}, count=1)
    if res:
        uuid = res[0][1][0][1][b'uuid'].decode()
        # init file rotation
        if save_to_file:
            rotate_file = False
            time_file = time.time()
            date_file = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            dir_data_uuid = os.path.join('../../data', uuid, str(type))
            dir_full_path = get_save_dir(dir_data_uuid, date_file[0:4], date_file[4:6], date_file[6:8])
            filename = '{}-{}-{}-{}-{}.meta_json.txt'.format(uuid, date_file[0:4], date_file[4:6], date_file[6:8], date_file[8:14])
            save_path = os.path.join(dir_full_path, filename)

        print('----    worker launched, uuid={} session_uuid={}'.format(uuid, session_uuid))
    else:
        print('Incorrect Stream, Closing worker: type={} session_uuid={}'.format(type, session_uuid))
        sys.exit(1)

    full_json = None

    # active session
    while full_json is None:

        res = redis_server_stream.xread({stream_name: id}, count=1)
        if res:
            new_id = res[0][1][0][0].decode()
            if id != new_id:
                id = new_id
                data = res[0][1][0][1]

                if id and data:
                    # reconstruct data
                    if buffer != b'':
                        data[b'message'] = b'{}{}'.format(buffer, data[b'message'])
                        buffer = b''
                    try:
                        full_json = json.loads()
                    except:
                        buffer += data[b'message']
                        # # TODO: filter too big json
                    redis_server_stream.xdel(stream_name, id)

                    # complete json received
                    if full_json:
                        if check_json_file(full_json):
                            break
                        # Incorrect Json
                        else:
                            on_error(session_uuid, type, 'Incorrect JSON object')
        else:
            # end session, no json received
            if redis_server_stream.sismember('ended_session', session_uuid):
                clean_db(session_uuid)
                print('----    Incomplete JSON object, DONE, uuid={} session_uuid={}'.format(uuid, session_uuid))
                sys.exit(0)
            else:
                time.sleep(10)

    # extract/parse JSON
    extended_type = full_json['type']
    if not redis_server_metadata.sismember('server:accepted_extended_type', extended_type):
        error_mess = 'Unsupported extended_type: {}'.format(extended_type)
        on_error(session_uuid, type_error, error_mess)
        print(error_mess)
        clean_db(session_uuid)
        sys.exit(1)

    # save json on disk
    if save_to_file:
        # get new save_path #use first or last received date ???
        dir_full_path = get_save_dir(dir_data_uuid, date_file[0:4], date_file[4:6], date_file[6:8])
        filename = '{}-{}-{}-{}-{}.meta_json.txt'.format(uuid, date_file[0:4], date_file[4:6], date_file[6:8], date_file[8:14])
        save_path = os.path.join(dir_full_path, filename)
        with open(save_path, 'w') as f:
            f.write(full_json)

    # change stream_name/type
    stream_name = stream_defined
    type = type_defined
    id = 0
    buffer = b''

    # Do the magic on 254 type
    while True:
        res = redis_server_stream.xread({stream_name: id}, count=1)
        if res:
            new_id = res[0][1][0][0].decode()
            if id != new_id:
                id = new_id
                data = res[0][1][0][1]

                if id and data:
                    print(data)

        else:
            # end session, no json received
            if redis_server_stream.sismember('ended_session', session_uuid):
                clean_db(session_uuid)
                print('----    Incomplete JSON object, DONE, uuid={} session_uuid={}'.format(uuid, session_uuid))
                sys.exit(0)
            else:
                time.sleep(10)