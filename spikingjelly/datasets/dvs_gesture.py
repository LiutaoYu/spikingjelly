import spikingjelly.datasets
import zipfile
import os
import threading
import tqdm
import numpy as np
import struct
from torchvision.datasets import utils
# https://www.research.ibm.com/dvsgesture/
# https://ibm.ent.box.com/s/3hiq58ww1pbbjrinh367ykfdf60xsfm8/folder/50167556794

labels_dict = {
'hand_clapping': 1,  # 注意不是从0开始
'right_hand_wave': 2,
'left_hand_wave': 3,
'right_arm_clockwise': 4,
'right_arm_counter_clockwise': 5,
'left_arm_clockwise': 6,
'left_arm_counter_clockwise': 7,
'arm_roll': 8,
'air_drums': 9,
'air_guitar': 10,
'other_gestures': 11
}  # gesture_mapping.csv
# url md5
resource = ['https://ibm.ent.box.com/s/3hiq58ww1pbbjrinh367ykfdf60xsfm8/folder/50167556794', '8a5c71fb11e24e5ca5b11866ca6c00a1']

class DvsGesture(spikingjelly.datasets.EventsFramesDatasetBase):
    @staticmethod
    def get_wh():
        return 128, 128

    @staticmethod
    def download_and_extract(download_root: str, extract_root: str):
        file_name = os.path.join(download_root, 'DvsGesture.tar.gz')
        if os.path.exists(file_name):
            if utils.check_md5(file_name, resource[1]):
                utils.extract_archive(download_root, extract_root)
            else:
                print(f'{file_name} corrupted.')


        print(f'Please download from {resource[0]} and save to {download_root} manually.')
        raise NotImplementedError


    @staticmethod
    def read_bin(file_name: str):
        # https://gitlab.com/inivation/dv/dv-python/
        with open(file_name, 'rb') as bin_f:
            # skip ascii header
            line = bin_f.readline()
            while line.startswith(b'#'):
                if line == b'#!END-HEADER\r\n':
                    break
                else:
                    line = bin_f.readline()

            txyp = {
                't': [],
                'x': [],
                'y': [],
                'p': []
            }
            while True:
                header = bin_f.read(28)
                if not header or len(header) == 0:
                    break

                # read header
                e_type = struct.unpack('H', header[0:2])[0]
                e_source = struct.unpack('H', header[2:4])[0]
                e_size = struct.unpack('I', header[4:8])[0]
                e_offset = struct.unpack('I', header[8:12])[0]
                e_tsoverflow = struct.unpack('I', header[12:16])[0]
                e_capacity = struct.unpack('I', header[16:20])[0]
                e_number = struct.unpack('I', header[20:24])[0]
                e_valid = struct.unpack('I', header[24:28])[0]

                data_length = e_capacity * e_size
                data = bin_f.read(data_length)
                counter = 0

                if e_type == 1:
                    while data[counter:counter + e_size]:
                        aer_data = struct.unpack('I', data[counter:counter + 4])[0]
                        timestamp = struct.unpack('I', data[counter + 4:counter + 8])[0] | e_tsoverflow << 31
                        x = (aer_data >> 17) & 0x00007FFF
                        y = (aer_data >> 2) & 0x00007FFF
                        pol = (aer_data >> 1) & 0x00000001
                        counter = counter + e_size
                        txyp['x'].append(x)
                        txyp['y'].append(y)
                        txyp['t'].append(timestamp)
                        txyp['p'].append(pol)
                else:
                    # non-polarity event packet, not implemented
                    pass
            txyp['x'] = np.asarray(txyp['x'])
            txyp['y'] = np.asarray(txyp['y'])
            txyp['t'] = np.asarray(txyp['t'])
            txyp['p'] = np.asarray(txyp['p'])
            return txyp


    @staticmethod
    def convert_aedat_dir_to_npy_dir(aedat_data_dir: str, npy_data_dir: str):
        # 将aedat_data_dir目录下的.aedat文件读取并转换成np保存的字典，保存在npy_data_dir目录
        print('convert events data from aedat to numpy format.')
        for aedat_file in tqdm.tqdm(utils.list_files(aedat_data_dir, '.aedat')):
            base_name = aedat_file[0: -6]
            events = DvsGesture.read_bin(os.path.join(aedat_data_dir, aedat_file))
            # 读取csv文件，获取各段的label，保存对应的数据和label
            events_csv = np.loadtxt(os.path.join(aedat_data_dir, base_name + '_labels.csv'),
                            dtype=np.uint32, delimiter=',', skiprows=1)
            index = 0
            index_l = 0
            index_r = 0
            for i in range(events_csv.shape[0]):
                label = events_csv[i][0]
                t_start = events_csv[i][1]
                t_end = events_csv[i][2]

                while True:
                    t = events['t'][index]
                    if t < t_start:
                        index += 1
                    else:
                        index_l = index  # 左闭
                        break
                while True:
                    t = events['t'][index]
                    if t < t_end:
                        index += 1
                    else:
                        index_r = index  # 右开
                        break
                # [index_l, index_r)
                j = 0
                while True:
                    file_name = os.path.join(npy_data_dir, f'{base_name}_{label}_{j}.npy')
                    if os.path.exists(file_name):  # 防止同一个aedat里存在多个相同label的数据段
                        j += 1
                    else:
                        np.save(file=file_name, arr={
                            't': events['t'][index_l:index_r],
                            'x': events['x'][index_l:index_r],
                            'y': events['y'][index_l:index_r],
                            'p': events['p'][index_l:index_r]
                        })
                        break


    @staticmethod
    def create_frames_dataset(events_data_dir: str, frames_data_dir: str, frames_num: int, split_by: str, normalization: str or None):
        width, height = DvsGesture.get_wh()
        spikingjelly.datasets.convert_events_dir_to_frames_dir(events_data_dir, frames_data_dir, '.npy',
                                                               np.load, height, width, frames_num, split_by, normalization)

    @staticmethod
    def get_events_item(file_name):
        return np.load(file_name), int(os.path.basename(file_name).split('_')[-2]) - 1

    @staticmethod
    def get_frames_item(file_name):
        return np.load(file_name), int(os.path.basename(file_name).split('_')[-2]) - 1

    def __init__(self, root: str, use_frame=True, frames_num=10, split_by='number', normalization='max'):
        events_root = os.path.join(root, 'events')
        if os.path.exists(events_root):
            # 如果root目录下存在events_root目录则认为数据集文件存在
            print(f'events data root {events_root} already exists.')
        else:
            os.mkdir(events_root)
            print(f'mkdir {events_root}')
            self.download_and_extract(root, events_root)

        events_npy_root = os.path.join(root, 'events_npy')
        if os.path.exists(events_npy_root):
            print(f'npy format events data root {events_npy_root} already exists')
        else:
            os.mkdir(events_npy_root)
            print(f'mkdir {events_root}')
            print('read evetns data from *.aedat and save to *.npy...')
            DvsGesture.convert_aedat_dir_to_npy_dir(events_root, events_npy_root)


        self.file_name = []  # 保存数据文件的路径
        self.use_frame = use_frame
        self.data_dir = None
        if use_frame:
            frames_root = os.path.join(root, f'frames_num_{frames_num}_split_by_{split_by}_normalization_{normalization}')
            if os.path.exists(frames_root):
                # 如果root目录下存在frames_root目录，则认为数据集文件存在
                print(f'frames data root {frames_root} already exists.')
            else:
                os.mkdir(frames_root)
                print(f'mkdir {frames_root}.')
                print('creating frames data..')
                DvsGesture.create_frames_dataset(events_npy_root, frames_root, frames_num, split_by, normalization)

            for sub_dir in utils.list_dir(frames_root, True):
                self.file_name.extend(utils.list_files(sub_dir, '.npy', True))
            self.data_dir = frames_root
            self.get_item_fun = DvsGesture.get_frames_item

        else:
            for sub_dir in utils.list_dir(events_npy_root, True):
                self.file_name.extend(utils.list_files(sub_dir, '.npy', True))
            self.data_dir = events_npy_root
            self.get_item_fun = DvsGesture.get_events_item

