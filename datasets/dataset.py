import glob
import os
import cv2
import torch
import numpy as np
import matplotlib.pyplot as plt
from torch.utils.data import Dataset
from .utils import get_camera_rays


def get_dataset(config):
    if config["dataset"] == "replica":
        dataset = ReplicaDataset
    
    elif config["dataset"] == "scannet":
        dataset = ScannetDataset

    elif config["dataset"] == "fastcamo_synth" or "kinect":
        dataset = FastCaMoDataset

    return dataset(config, config["data"]["datadir"], trainskip=config["data"]["trainskip"],
                   downsample_factor=config["data"]["downsample"], sc_factor=config["data"]["sc_factor"])


class BaseDataset(Dataset):
    def __init__(self, cfg):
        self.png_depth_scale = cfg["cam"]["png_depth_scale"]
        self.H, self.W = cfg["cam"]["H"]//cfg["data"]["downsample"],cfg["cam"]["W"]//cfg["data"]["downsample"]

        self.fx, self.fy = cfg["cam"]["fx"]//cfg["data"]["downsample"], cfg["cam"]["fy"]//cfg["data"]["downsample"]
        self.cx, self.cy = cfg["cam"]["cx"]//cfg["data"]["downsample"], cfg["cam"]["cy"]//cfg["data"]["downsample"]
        self.distortion = np.array(cfg["cam"]["distortion"]) if 'distortion' in cfg["cam"] else None
        self.crop_size = cfg["cam"]["crop_edge"] if 'crop_edge' in cfg["cam"] else 0
        self.ignore_w = cfg["tracking"]["ignore_edge_W"]
        self.ignore_h = cfg["tracking"]["ignore_edge_H"]

        self.total_pixels = (self.H - self.crop_size*2) * (self.W - self.crop_size*2)  # pixel number of a frame

    def __len__(self):
        raise NotImplementedError()
    
    def __getitem__(self, index):
        raise NotImplementedError()


class ReplicaDataset(BaseDataset):
    def __init__(self, cfg, basedir, trainskip=1, downsample_factor=1, translation=0.0, sc_factor=1., crop=0):
        super(ReplicaDataset, self).__init__(cfg)

        self.basedir = basedir
        self.trainskip = trainskip
        self.downsample_factor = downsample_factor
        self.translation = translation
        self.sc_factor = sc_factor
        self.crop = crop
        self.img_files = sorted(glob.glob(f'{self.basedir}/results/frame*.jpg'))
        self.depth_paths = sorted(
            glob.glob(f'{self.basedir}/results/depth*.png'))
        self.load_poses(os.path.join(self.basedir, 'traj.txt'))
        

        self.rays_d = None
        self.tracking_mask = None
        self.frame_ids = range(0, len(self.img_files))
        self.num_frames = len(self.frame_ids)
    
    def __len__(self):
        return self.num_frames

    
    def __getitem__(self, index):
        color_path = self.img_files[index]
        depth_path = self.depth_paths[index]

        color_data = cv2.imread(color_path)
        if '.png' in depth_path:
            depth_data = cv2.imread(depth_path, cv2.IMREAD_UNCHANGED)
        elif '.exr' in depth_path:
            raise NotImplementedError()
        if self.distortion is not None:
            raise NotImplementedError()

        color_data = cv2.cvtColor(color_data, cv2.COLOR_BGR2RGB)
        color_data = color_data / 255.
        depth_data = depth_data.astype(np.float32) / self.png_depth_scale * self.sc_factor

        H, W = depth_data.shape
        color_data = cv2.resize(color_data, (W, H))

        if self.downsample_factor > 1:
            H = H // self.downsample_factor
            W = W // self.downsample_factor
            color_data = cv2.resize(color_data, (W, H), interpolation=cv2.INTER_AREA)
            depth_data = cv2.resize(depth_data, (W, H), interpolation=cv2.INTER_NEAREST)

        if self.rays_d is None:
            self.rays_d = get_camera_rays(self.H, self.W, self.fx, self.fy, self.cx, self.cy)
       

        color_data = torch.from_numpy(color_data.astype(np.float32))
        depth_data = torch.from_numpy(depth_data.astype(np.float32))

        ret = {
            "frame_id": self.frame_ids[index],
            "c2w":  self.poses[index],
            "rgb": color_data,
            "depth": depth_data,
            "direction": self.rays_d,
        }

        return ret


    def load_poses(self, path):
        self.poses = []
        with open(path, "r") as f:
            lines = f.readlines()
        for i in range(len(self.img_files)):
            line = lines[i]
            c2w = np.array(list(map(float, line.split()))).reshape(4, 4)
            c2w[:3, 1] *= -1
            c2w[:3, 2] *= -1
            c2w[:3, 3] *= self.sc_factor
            c2w = torch.from_numpy(c2w).float()
            self.poses.append(c2w)


class ScannetDataset(BaseDataset):
    def __init__(self, cfg, basedir, trainskip=1, downsample_factor=1, translation=0.0, sc_factor=1., crop=0):
        super(ScannetDataset, self).__init__(cfg)

        self.config = cfg
        self.basedir = basedir  # input dir of this sequence
        self.trainskip = trainskip
        self.downsample_factor = downsample_factor
        self.translation = translation
        self.sc_factor = sc_factor
        self.crop = crop
        self.img_files = sorted( glob.glob( os.path.join(self.basedir, 'color', '*.jpg') ), key=lambda x: int(os.path.basename(x)[:-4]) )
        self.depth_paths = sorted( glob.glob(os.path.join(self.basedir, 'depth', '*.png') ), key=lambda x: int(os.path.basename(x)[:-4]) )

        if self.config["data"]["starting_frame"] > 0:
            self.img_files = [ i for i in self.img_files if int(os.path.basename(i)[:-4]) >= self.config["data"]["starting_frame"] ]
            self.depth_paths = [ i for i in self.depth_paths if int(os.path.basename(i)[:-4]) >= self.config["data"]["starting_frame"] ]

        self.load_poses(os.path.join(self.basedir, 'pose'))  # filling self.poses
        # self.depth_cleaner = cv2.rgbd.DepthCleaner_create(cv2.CV_32F, 5)

        self.frame_ids = range(0, len(self.img_files))
        self.num_frames = len(self.frame_ids)

        if self.config["cam"]["crop_edge"] > 0:
            self.H -= self.config["cam"]["crop_edge"] * 2
            self.W -= self.config["cam"]["crop_edge"] * 2
            self.cx -= self.config["cam"]["crop_edge"]
            self.cy -= self.config["cam"]["crop_edge"]

        self.rays_d = get_camera_rays(self.H, self.W, self.fx, self.fy, self.cx, self.cy)  # Tensor(H, W, 3)
   
    def __len__(self):
        return self.num_frames
  
    def __getitem__(self, index):
        color_path = self.img_files[index]
        depth_path = self.depth_paths[index]

        color_data = cv2.imread(color_path)
        if '.png' in depth_path:
            depth_data = cv2.imread(depth_path, cv2.IMREAD_UNCHANGED)
        elif '.exr' in depth_path:
            raise NotImplementedError()
        if self.distortion is not None:
            raise NotImplementedError()

        color_data = cv2.cvtColor(color_data, cv2.COLOR_BGR2RGB)
        color_data = color_data / 255.
        depth_data = depth_data.astype(np.float32) / self.png_depth_scale * self.sc_factor

        H, W = depth_data.shape
        color_data = cv2.resize(color_data, (W, H))

        if self.downsample_factor > 1:
            H = H // self.downsample_factor
            W = W // self.downsample_factor
            self.fx = self.fx // self.downsample_factor
            self.fy = self.fy // self.downsample_factor
            color_data = cv2.resize(color_data, (W, H), interpolation=cv2.INTER_AREA)
            depth_data = cv2.resize(depth_data, (W, H), interpolation=cv2.INTER_NEAREST)
        
        edge = self.config["cam"]["crop_edge"]
        if edge > 0:
            # crop image edge, there are invalid value on the edge of the color image
            color_data = color_data[edge:-edge, edge:-edge]
            depth_data = depth_data[edge:-edge, edge:-edge]

        if self.rays_d is None:
            self.rays_d = get_camera_rays(self.H, self.W, self.fx, self.fy, self.cx, self.cy)

        color_data = torch.from_numpy(color_data.astype(np.float32))
        depth_data = torch.from_numpy(depth_data.astype(np.float32))

        ret = {
            "frame_id": self.frame_ids[index],
            "c2w":  self.poses[index],
            "rgb": color_data,
            "depth": depth_data,
            "direction": self.rays_d
        }
        return ret


    def load_poses(self, path):
        self.poses = []  # gt poses
        pose_paths = sorted(glob.glob(os.path.join(path, '*.txt')), key=lambda x: int(os.path.basename(x)[:-4]))

        counter = 0
        for pose_path in pose_paths:
            # set starting frame
            if self.config["data"]["starting_frame"] > 0:
                if counter < self.config["data"]["starting_frame"]:
                    counter += 1
                    continue

            with open(pose_path, "r") as f:
                lines = f.readlines()
            ls = []
            for line in lines:
                l = list(map(float, line.split(' ')))
                ls.append(l)
            c2w = np.array(ls).reshape(4, 4)  # ndarray(4, 4)
            c2w[:3, 1] *= -1
            c2w[:3, 2] *= -1
            c2w = torch.from_numpy(c2w).float()
            self.poses.append(c2w)
            counter += 1


class FastCaMoDataset(BaseDataset):
    def __init__(self, cfg, basedir, trainskip=1, downsample_factor=1, translation=0.0, sc_factor=1., crop=0):
        super(FastCaMoDataset, self).__init__(cfg)

        self.config = cfg
        self.basedir = basedir  # input dir of this sequence
        self.trainskip = trainskip
        self.downsample_factor = downsample_factor
        self.translation = translation
        self.sc_factor = sc_factor
        self.crop = crop
        self.img_files = sorted(glob.glob(os.path.join(self.basedir, 'color', '*.png')), key=lambda x: float(os.path.basename(x)[:-4]))
        self.depth_paths = sorted(glob.glob(os.path.join(self.basedir, 'depth', '*.png')), key=lambda x: float(os.path.basename(x)[:-4]))



        if self.config["data"]["starting_frame"] > 0:
            self.img_files = [i for i in self.img_files if int(os.path.basename(i)[:-4]) >= self.config["data"]["starting_frame"]]
            self.depth_paths = [i for i in self.depth_paths if int(os.path.basename(i)[:-4]) >= self.config["data"]["starting_frame"]]

        self.load_poses(os.path.join(self.basedir, 'pose'))  # filling self.poses

        self.frame_ids = range(0, len(self.img_files))

        self.num_frames = len(self.frame_ids)

        if self.config["cam"]["crop_edge"] > 0:
            self.H -= self.config["cam"]["crop_edge"] * 2
            self.W -= self.config["cam"]["crop_edge"] * 2
            self.cx -= self.config["cam"]["crop_edge"]
            self.cy -= self.config["cam"]["crop_edge"]

        # print(f"fx: {self.fx}, fy: {self.fy}, cx: {self.cx}, cy: {self.cy}")

        self.rays_d = get_camera_rays(self.H, self.W, self.fx, self.fy, self.cx, self.cy) # Tensor(3052, 4076, 3)

    def __len__(self):
        return self.num_frames

    def __getitem__(self, index):
        color_path = self.img_files[index]
        depth_path = self.depth_paths[index]

        color_data = cv2.imread(color_path)
        if '.png' in depth_path:
            depth_data = cv2.imread(depth_path, cv2.IMREAD_UNCHANGED)
        elif '.exr' in depth_path:
            raise NotImplementedError()
        if self.distortion is not None:
            raise NotImplementedError()

        color_data = cv2.cvtColor(color_data, cv2.COLOR_BGR2RGB)
        color_data = color_data / 255.
        depth_data = depth_data.astype(np.float32) / self.png_depth_scale * self.sc_factor
        H, W = depth_data.shape
        color_data = cv2.resize(color_data, (W, H))

        if self.downsample_factor > 1:
            H = H // self.downsample_factor
            W = W // self.downsample_factor
            self.fx = self.fx // self.downsample_factor
            self.fy = self.fy // self.downsample_factor
            color_data = cv2.resize(color_data, (W, H), interpolation=cv2.INTER_AREA)
            depth_data = cv2.resize(depth_data, (W, H), interpolation=cv2.INTER_NEAREST)

        edge = self.config["cam"]["crop_edge"]
        if edge > 0:
            # crop image edge, there are invalid value on the edge of the color image
            color_data = color_data[edge:-edge, edge:-edge]
            depth_data = depth_data[edge:-edge, edge:-edge]

        if self.rays_d is None:
            self.rays_d = get_camera_rays(self.H, self.W, self.fx, self.fy, self.cx, self.cy)

        color_data = torch.from_numpy(color_data.astype(np.float32))
        depth_data = torch.from_numpy(depth_data.astype(np.float32))

        ret = {
            "frame_id": self.frame_ids[index],
            "c2w": self.poses[index],
            "rgb": color_data,
            "depth": depth_data,
            "direction": self.rays_d
        }
        return ret

    def load_poses(self, path):
        self.poses = []  # gt poses
        pose_paths = sorted(glob.glob(os.path.join(path, '*.txt')), key=lambda x: int(os.path.basename(x)[:-4]))

        counter = 0
        for pose_path in pose_paths:
            # set starting frame
            if self.config["data"]["starting_frame"] > 0:
                if counter < self.config["data"]["starting_frame"]:
                    counter += 1
                    continue

            with open(pose_path, "r") as f:
                lines = f.readlines()
            ls = []
            for line in lines:
                l = list(map(float, line.split(' ')))
                ls.append(l)
            c2w = np.array(ls).reshape(4, 4)  # ndarray(4, 4)
            c2w[:3, 1] *= -1
            c2w[:3, 2] *= -1
            c2w = torch.from_numpy(c2w).float()
            self.poses.append(c2w)
            counter += 1