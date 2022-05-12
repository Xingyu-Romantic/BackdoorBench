'''
@misc{huang2022backdoor,
  title={Backdoor defense via decoupling the training process},
  author={Huang, Kunzhe and Li, Yiming and Wu, Baoyuan and Qin, Zhan and Ren, Kui},
  year={2022},
  publisher={ICLR}
}

code : https://github.com/SCLBD/DBD
'''
import logging
import time
import argparse
import shutil
import sys
import os


sys.path.append('../')
sys.path.append(os.getcwd())

import numpy as np
import torch
import torch.nn as nn
import torch.distributed as dist
import torch.multiprocessing as mp

import yaml

# from torch.utils.data.distributed import DistributedSampler
from pprint import pprint, pformat
from utils.aggregate_block.dataset_and_transform_generate import get_transform

from data.utils import (
    gen_poison_idx,
    get_bd_transform,
    get_dataset,
    get_loader,
    get_semi_idx,
)
from data.dataset import PoisonLabelDataset, SelfPoisonDataset, MixMatchDataset
from utils.aggregate_block.fix_random import fix_random
from utils.save_load_attack import load_attack_result
from utils_db.box import get_information
from model.model import SelfModel, LinearModel
from model.utils import (
    get_network_dbd,
    load_state,
    get_criterion,
    get_network,
    get_optimizer,
    get_saved_epoch,
    get_scheduler,
)
from utils.bd_dataset import prepro_cls_DatasetBD, xy_iter
from utils.nCHW_nHWC import nCHW_to_nHWC
from utils_db.setup import (
    load_config,
    get_logger,
    get_saved_dir,
    get_storage_dir,
    set_seed,
)
from utils_db.trainer.log import result2csv
from utils_db.trainer.simclr import simclr_train




from utils_db.trainer.semi import mixmatch_train
from utils_db.trainer.simclr import linear_test, poison_linear_record, poison_linear_train

def get_args():
    parser = argparse.ArgumentParser()
    
    parser.add_argument('--device', type=str, help='cuda, cpu')
    parser.add_argument('--checkpoint_load', type=str)
    parser.add_argument('--checkpoint_save', type=str)
    parser.add_argument('--log', type=str)
    parser.add_argument("--data_root", type=str)

    parser.add_argument('--dataset', type=str, help='mnist, cifar10, gtsrb, celeba, tiny') 
    parser.add_argument("--num_classes", type=int)
    parser.add_argument("--input_height", type=int)
    parser.add_argument("--input_width", type=int)
    parser.add_argument("--input_channel", type=int)

    parser.add_argument('--epochs', type=int)
    parser.add_argument('--batch_size', type=int)
    parser.add_argument("--num_workers", type=float)
    parser.add_argument('--lr', type=float)

    parser.add_argument('--attack', type=str)
    parser.add_argument('--poison_rate', type=float)
    parser.add_argument('--target_type', type=str, help='all2one, all2all, cleanLabel') 
    parser.add_argument('--target_label', type=int)
    parser.add_argument('--trigger_type', type=str, help='squareTrigger, gridTrigger, fourCornerTrigger, randomPixelTrigger, signalTrigger, trojanTrigger')

    parser.add_argument('--seed', type=str, help='random seed')
    parser.add_argument('--index', type=str, help='index of clean data')
    parser.add_argument('--model', type=str, help='resnet18')
    parser.add_argument('--result_file', type=str, help='the location of result')

    # DBD
    parser.add_argument('--epoch_warmup', type=str, help='the location of result')
    parser.add_argument('--batch_size_self', type=str, help='the location of result')
    parser.add_argument('--epochs_self', type=str, help='the location of result')
    parser.add_argument('--temperature', type=str, help='the location of result')
    parser.add_argument('--epsilon', type=str, help='the location of result')
    parser.add_argument('--epoch_self', type=str, help='the location of result')
    

    arg = parser.parse_args()

    print(arg)
    return arg


def dbd(args,result):

    logFormatter = logging.Formatter(
        fmt='%(asctime)s [%(levelname)-8s] [%(filename)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d:%H:%M:%S',
    )
    logger = logging.getLogger()
    # logFormatter = logging.Formatter("%(asctime)s [%(levelname)-5.5s] %(message)s")
    if args.log is not None and args.log != '':
        fileHandler = logging.FileHandler(os.getcwd() + args.log + '/' + time.strftime("%Y_%m_%d_%H_%M_%S", time.localtime()) + '.log')
    else:
        fileHandler = logging.FileHandler(os.getcwd() + './log' + '/' + time.strftime("%Y_%m_%d_%H_%M_%S", time.localtime()) + '.log')
    fileHandler.setFormatter(logFormatter)
    logger.addHandler(fileHandler)

    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(logFormatter)
    logger.addHandler(consoleHandler)

    logger.setLevel(logging.INFO)
    logging.info(pformat(args.__dict__))

    fix_random(args.seed)

    logging.info("===Setup running===")
    # parser = argparse.ArgumentParser()
    #parser.add_argument("--config", default="./config/pretrain/example.yaml")
    #parser.add_argument("--gpu", default="0", type=str)
    # parser.add_argument(
    #     "--resume",
    #     default="",
    #     type=str,
    #     help="checkpoint name (empty string means the latest checkpoint)\
    #         or False (means training from scratch).",
    # )
    if args.checkpoint_load == None:
        args.resume = 'False' 
    else :
        args.resume = args.checkpoint_load
    # parser.add_argument("--amp", default=False, action="store_true")
    # parser.add_argument("--num_stage_epochs", default=100, type=int)
    # parser.add_argument("--min_interval", default=20, type=int)
    # parser.add_argument("--max_interval", default=100, type=int)
    # parser.add_argument(
    #     "--world-size",
    #     default=1,
    #     type=int,
    #     help="number of nodes for distributed training",
    # )
    # parser.add_argument(
    #     "--rank", default=0, type=int, help="node rank for distributed training"
    # )
    # parser.add_argument(
    #     "--dist-port",
    #     default="23456",
    #     type=str,
    #     help="port used to set up distributed training",
    # )
    # args = parser.parse_args()
    
    if args.dataset == 'cifar10':
        config_file = './defense/dbd/config_z/pretrain/' + 'signalTrigger/' + args.dataset + '/example.yaml'
    else:
        config_file = './defense/dbd/config_z/pretrain/' + 'signalTrigger/imagenet/example.yaml'
    config_ori, inner_dir, config_name = load_config(config_file)
    gpu = int(os.environ['CUDA_VISIBLE_DEVICES']) 
    logging.info("===Prepare data===")
    information = get_information(args,result,config_ori)
   
    # saved_epoch = get_saved_epoch(
    #     config["num_epochs"],
    #     args.num_stage_epochs,
    #     args.min_interval,
    #     args.max_interval,
    # )
    # logger.info("Set saved epoch to {}".format(saved_epoch))

    self_poison_train_loader = information['self_poison_train_loader']
    self_model = information['self_model']
    criterion = information['criterion']
    optimizer = information['optimizer']
    scheduler = information['scheduler']
    resumed_epoch = information['resumed_epoch']

    for epoch in range(args.epoch_self - resumed_epoch):
        # if args.distributed:
        #     self_poison_train_sampler.set_epoch(epoch)
        # logger.info(
        #     "===Epoch: {}/{}===".format(epoch + resumed_epoch + 1, config["num_epochs"])
        # )
        # logger.info("SimCLR training...")
        self_train_result = simclr_train(
            self_model, self_poison_train_loader, criterion, optimizer, logger, False
        )

        if scheduler is not None:
            scheduler.step()
            logger.info(
                "Adjust learning rate to {}".format(optimizer.param_groups[0]["lr"])
            )

        # Save result and checkpoint.
        # if not args.distributed or (args.distributed and gpu == 0):
        result_self = {"self_train": self_train_result}
        # result2csv(result, args.log_dir)

        saved_dict = {
            "epoch": epoch + resumed_epoch + 1,
            "result": result_self,
            "model_state_dict": self_model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
        }
        if scheduler is not None:
            saved_dict["scheduler_state_dict"] = scheduler.state_dict()

        ckpt_path = os.path.join(os.getcwd() + args.checkpoint_save, "latest_model.pt")
        torch.save(saved_dict, ckpt_path)
        logger.info("Save the latest model to {}".format(ckpt_path))
        
        # ckpt_path = os.path.join(
        #     os.getcwd() + args.checkpoint_save, "epoch{}.pt".format(epoch + resumed_epoch + 1)
        # )
        # torch.save(saved_dict, ckpt_path)
        # logger.info("Save the model in saved epoch to {}".format(ckpt_path))

    ####self
    # parser = argparse.ArgumentParser()
    #parser.add_argument("--config", default="./config/pretrain/example.yaml")
    #parser.add_argument("--gpu", default="0", type=str)
    # parser.add_argument(
    #     "--resume",
    #     default="",
    #     type=str,
    #     help="checkpoint name (empty string means the latest checkpoint)\
    #         or False (means training from scratch).",
    # )
    # args.resume = args.checkpoint_load
    # parser.add_argument("--amp", default=False, action="store_true")
    # parser.add_argument("--num_stage_epochs", default=100, type=int)
    # parser.add_argument("--min_interval", default=20, type=int)
    # parser.add_argument("--max_interval", default=100, type=int)
    # parser.add_argument(
    #     "--world-size",
    #     default=1,
    #     type=int,
    #     help="number of nodes for distributed training",
    # )
    # parser.add_argument(
    #     "--rank", default=0, type=int, help="node rank for distributed training"
    # )
    # parser.add_argument(
    #     "--dist-port",
    #     default="23456",
    #     type=str,
    #     help="port used to set up distributed training",
    # )
    # args = parser.parse_args()
    
    ####需要修改trigger类型
    if args.dataset == 'cifar10':
        config_file_semi = './defense/dbd/config_z/semi/' + 'blend/' + args.dataset + '/example.yaml'
    else:
        config_file_semi = './defense/dbd/config_z/semi/' + 'blend/imagenet/example.yaml'
 
    # config, inner_dir, config_name = load_config(args.config)
    # args.saved_dir, args.log_dir = get_saved_dir(
    #     config, inner_dir, config_name, args.resume
    # )
    # shutil.copy2(args.config, args.saved_dir)
    # args.storage_dir, args.ckpt_dir, _ = get_storage_dir(
    #     config, inner_dir, config_name, args.resume
    # )
    # shutil.copy2(args.config, args.storage_dir)
    # set_seed(**config["seed"])

    # os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu

    #####sim supervised
    finetune_config, finetune_inner_dir, finetune_config_name = load_config(config_file_semi)
    pretrain_config, pretrain_inner_dir, pretrain_config_name = load_config(
        config_file
    )
    # finetune_config["pretrain_config_path"]
    # pretrain_saved_dir, _ = get_saved_dir(
    #     pretrain_config, pretrain_inner_dir, pretrain_config_name
    # )
    # _, pretrain_ckpt_dir, _ = get_storage_dir(
    #     pretrain_config, pretrain_inner_dir, pretrain_config_name
    # )
    pretrain_ckpt_path = ckpt_path
    # merge the pretrain and finetune config
    pretrain_config.update(finetune_config)
    config = pretrain_config

    ####替换某些参数
    pretrain_config['warmup']['criterion']['sce']['num_classes'] = args.num_classes
    pretrain_config['warmup']['num_epochs'] = args.epoch_warmup

    #config = pretrain_config
    # saved_dir, log_dir = get_saved_dir(
    #     config, finetune_inner_dir, finetune_config_name, args.resume
    # )
    # shutil.copy2(args.config, saved_dir)
    # storage_dir, ckpt_dir, record_dir = get_storage_dir(
    #     config, finetune_inner_dir, finetune_config_name, args.resume,
    # )
    # shutil.copy2(args.config, storage_dir)
    # logger = get_logger(log_dir, "finetune.log", args.resume)
    # set_seed(**config["seed"])
    # logger.info("Load finetune config from: {}".format(args.config))
    # logger.info(
    #     "Load pretrain config from: {}".format(finetune_config["pretrain_config_path"])
    # )

    logging.info("\n===Prepare data===")
    # bd_config = config["backdoor"]
    # logger.info("Load backdoor config:\n{}".format(bd_config))
    # bd_transform = get_bd_transform(bd_config)
    # target_label = bd_config["target_label"]
    # poison_ratio = bd_config["poison_ratio"]

    # pre_transform = get_transform(config["transform"]["pre"])
    # train_primary_transform = get_transform(config["transform"]["train"]["primary"])
    # train_remaining_transform = get_transform(config["transform"]["train"]["remaining"])
    # train_transform = {
    #     "pre": pre_transform,
    #     "primary": train_primary_transform,
    #     "remaining": train_remaining_transform,
    # }
    # logger.info("Training transformations:\n {}".format(train_transform))
    # test_primary_transform = get_transform(config["transform"]["test"]["primary"])
    # test_remaining_transform = get_transform(config["transform"]["test"]["remaining"])
    # test_transform = {
    #     "pre": pre_transform,
    #     "primary": test_primary_transform,
    #     "remaining": test_remaining_transform,
    # }
    # logger.info("Test transformations:\n {}".format(test_transform))

    # logger.info("Load dataset from: {}".format(config["dataset_dir"]))
    # clean_train_data = get_dataset(
    #     config["dataset_dir"], train_transform, prefetch=config["prefetch"]
    # )
    # # Load poisoned training index from pretrain.
    # poison_idx_path = os.path.join(pretrain_saved_dir, "poison_idx.npy")
    # poison_train_idx = np.load(poison_idx_path)
    # poison_train_data = PoisonLabelDataset(
    #     clean_train_data, bd_transform, poison_train_idx, target_label
    # )
    # clean_test_data = get_dataset(
    #     config["dataset_dir"], test_transform, train=False, prefetch=config["prefetch"]
    # )
    # poison_test_idx = gen_poison_idx(clean_test_data, target_label)
    # poison_test_data = PoisonLabelDataset(
    #     clean_test_data, bd_transform, poison_test_idx, target_label
    # )

    # train_primary_transform = get_transform(pretrain_config["transform"]["train"]["primary"])
    # train_remaining_transform = get_transform(pretrain_config["transform"]["train"]["remaining"])
    # train_transform = transforms.Compose([train_primary_transform,train_remaining_transform])
    train_transform = get_transform(args.dataset, *([args.input_height,args.input_width]) , train = True)
    x = result['bd_train']['x']
    y = result['bd_train']['y']
    # data_set = list(zip(x,y))
    dataset_ori = xy_iter(
        x,y,train_transform
    )
    dataset = PoisonLabelDataset(dataset_ori, train_transform, np.zeros(len(dataset_ori)), True)
    poison_train_loader = torch.utils.data.DataLoader(dataset, batch_size=args.batch_size, num_workers=args.num_workers,drop_last=False, shuffle=True,pin_memory=True)
    poison_eval_loader = torch.utils.data.DataLoader(dataset, batch_size=args.batch_size, num_workers=args.num_workers,drop_last=False, shuffle=False,pin_memory=True)
    
    # test_primary_transform = get_transform(pretrain_config["transform"]["test"]["primary"])
    # test_remaining_transform = get_transform(pretrain_config["transform"]["test"]["remaining"])
    # test_transform = transforms.Compose([test_primary_transform,test_remaining_transform])
    test_transform = get_transform(args.dataset, *([args.input_height,args.input_width]) , train = False)
    x = result['bd_test']['x']
    y = result['bd_test']['y']
    dataset_ori_bd = xy_iter(
        x,y,train_transform
    )
    dataset_te_bd = PoisonLabelDataset(dataset_ori_bd, test_transform, np.zeros(len(dataset_ori_bd)), False)
    poison_test_loader = torch.utils.data.DataLoader(dataset_te_bd, batch_size=args.batch_size, num_workers=args.num_workers,drop_last=False, shuffle=False,pin_memory=True)

    x = result['clean_test']['x']
    y = result['clean_test']['y']
    dataset_ori_cl = xy_iter(
        x,y,train_transform
    )
    dataset_te_cl = PoisonLabelDataset(dataset_ori_cl, test_transform, np.zeros(len(dataset_ori_cl)), False)
    clean_test_loader = torch.utils.data.DataLoader(dataset_te_cl, batch_size=args.batch_size, num_workers=args.num_workers,drop_last=False, shuffle=False,pin_memory=True)

    # logger.info("\n===Setup training===")
    # gpu = int(args.gpu)
    # torch.cuda.set_device(gpu)
    # logger.info("Set gpu to: {}".format(gpu))
    backbone = get_network_dbd(args)
    # logger.info("Create network: {}".format(config["network"]))
    self_model = SelfModel(backbone)
    self_model = self_model.to(args.device)
    # Load backbone from the pretrained model.
    load_state(
        self_model, pretrain_config["pretrain_checkpoint"], pretrain_ckpt_path, args.device, logger
    )
    linear_model = LinearModel(backbone, backbone.feature_dim, args.num_classes)
    linear_model.linear.to(args.device)
    warmup_criterion = get_criterion(pretrain_config["warmup"]["criterion"])
    logger.info("Create criterion: {} for warmup".format(warmup_criterion))
    warmup_criterion = warmup_criterion.to(args.device)
    semi_criterion = get_criterion(pretrain_config["semi"]["criterion"])
    semi_criterion = semi_criterion.to(args.device)
    logger.info("Create criterion: {} for semi-training".format(semi_criterion))
    optimizer = get_optimizer(linear_model, pretrain_config["optimizer"])
    logger.info("Create optimizer: {}".format(optimizer))
    scheduler = get_scheduler(optimizer, pretrain_config["lr_scheduler"])
    logger.info("Create learning rete scheduler: {}".format(pretrain_config["lr_scheduler"]))
    if args.checkpoint_load == '' or args.checkpoint_load is None:
        resume = 'False'
    resumed_epoch, best_acc, best_epoch = load_state(
        linear_model,
        resume,
        args.checkpoint_load,
        gpu,
        logger,
        optimizer,
        scheduler,
        is_best=True,
    )

    num_epochs = args.epoch_warmup + args.epochs
    for epoch in range(num_epochs - resumed_epoch):
        logger.info("===Epoch: {}/{}===".format(epoch + resumed_epoch + 1, num_epochs))
        if (epoch + resumed_epoch + 1) <= args.epoch_warmup:
            logger.info("Poisoned linear warmup...")
            poison_train_result = poison_linear_train(
                linear_model, poison_train_loader, warmup_criterion, optimizer, logger,
            )
        else:
            record_list = poison_linear_record(
                linear_model, poison_eval_loader, warmup_criterion
            )
            logger.info("Mining clean data from poisoned dataset...")
            semi_idx = get_semi_idx(record_list, args.epsilon, logger)
            xdata = MixMatchDataset(dataset, semi_idx, labeled=True)
            udata = MixMatchDataset(dataset, semi_idx, labeled=False)
            xloader = get_loader(
                xdata, pretrain_config["semi"]["loader"], shuffle=True, drop_last=True
            )
            uloader = get_loader(
                udata, pretrain_config["semi"]["loader"], shuffle=True, drop_last=True
            )
            logger.info("MixMatch training...")
            poison_train_result = mixmatch_train(
                args,
                linear_model,
                xloader,
                uloader,
                semi_criterion,
                optimizer,
                epoch,
                logger,
                **pretrain_config["semi"]["mixmatch"]
            )
        logger.info("Test model on clean data...")
        clean_test_result = linear_test(
            linear_model, clean_test_loader, warmup_criterion, logger
        )
        logger.info("Test model on poison data...")
        poison_test_result = linear_test(
            linear_model, poison_test_loader, warmup_criterion, logger
        )
        if scheduler is not None:
            scheduler.step()
            logger.info(
                "Adjust learning rate to {}".format(optimizer.param_groups[0]["lr"])
            )
        result = {
            "poison_train": poison_train_result,
            "poison_test": poison_test_result,
            "clean_test": clean_test_result,
        }
        result2csv(result, os.getcwd() + args.log)

        is_best = False
        if clean_test_result["acc"] > best_acc:
            is_best = True
            best_acc = clean_test_result["acc"]
            best_epoch = epoch + resumed_epoch + 1
        logger.info("Best test accuaracy {} in epoch {}".format(best_acc, best_epoch))

        saved_dict = {
            "epoch": epoch + resumed_epoch + 1,
            "result": result,
            "model_state_dict": linear_model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "best_acc": best_acc,
            "best_epoch": best_epoch,
        }
        if scheduler is not None:
            saved_dict["scheduler_state_dict"] = scheduler.state_dict()

        if is_best:
            ckpt_path = os.path.join(os.getcwd() + args.checkpoint_save, "best_model.pt")
            torch.save(saved_dict, ckpt_path)
            logger.info("Save the best model to {}".format(ckpt_path))
        ckpt_path = os.path.join(os.getcwd() + args.checkpoint_save, "latest_model.pt")
        torch.save(saved_dict, ckpt_path)
        logger.info("Save the latest model to {}".format(ckpt_path))

    result = {}
    result['model'] = linear_model
    return result

if __name__ == '__main__':
    
   ### 1. basic setting: args
    args = get_args()
    with open("./defense/dbd/config.yaml", 'r') as stream: 
        config = yaml.safe_load(stream) 
    config.update({k:v for k,v in args.__dict__.items() if v is not None})
    args.__dict__ = config
    if args.dataset == "mnist":
        args.num_classes = 10
        args.input_height = 28
        args.input_width = 28
        args.input_channel = 1
    elif args.dataset == "cifar10":
        args.num_classes = 10
        args.input_height = 32
        args.input_width = 32
        args.input_channel = 3
    elif args.dataset == "cifar100":
        args.num_classes = 100
        args.input_height = 32
        args.input_width = 32
        args.input_channel = 3
    elif args.dataset == "gtsrb":
        args.num_classes = 43
        args.input_height = 32
        args.input_width = 32
        args.input_channel = 3
    elif args.dataset == "celeba":
        args.num_classes = 8
        args.input_height = 64
        args.input_width = 64
        args.input_channel = 3
    elif args.dataset == "tiny":
        args.num_classes = 200
        args.input_height = 64
        args.input_width = 64
        args.input_channel = 3
    else:
        raise Exception("Invalid Dataset")
    
    save_path = '/record/' + args.result_file
    if args.checkpoint_save is None:
        args.checkpoint_save = save_path + '/record/defence/dbd/'
        if not (os.path.exists(os.getcwd() + args.checkpoint_save)):
            os.makedirs(os.getcwd() + args.checkpoint_save) 
    if args.log is None:
        args.log = save_path + '/saved/dbd/'
        if not (os.path.exists(os.getcwd() + args.log)):
            os.makedirs(os.getcwd() + args.log) 
    args.save_path = save_path
    
    ### 2. attack result(model, train data, test data)
    result = load_attack_result(os.getcwd() + save_path + '/attack_result.pt')
    
    ### 3. dbd defense:
    print("Continue training...")
    result_defense = dbd(args,result)

    ### 4. test the result and get ASR, ACC, RC
    result_defense['model'].eval()
    result_defense['model'].to(args.device) 
    tran = get_transform(args.dataset, *([args.input_height,args.input_width]) , train = False)
    x = result['bd_test']['x']
    y = result['bd_test']['y']
    data_bd_test = list(zip(x,y))
    data_bd_testset = prepro_cls_DatasetBD(
        full_dataset_without_transform=data_bd_test,
        poison_idx=np.zeros(len(data_bd_test)),  # one-hot to determine which image may take bd_transform
        bd_image_pre_transform=None,
        bd_label_pre_transform=None,
        ori_image_transform_in_loading=tran,
        ori_label_transform_in_loading=None,
        add_details_in_preprocess=False,
    )
    data_bd_loader = torch.utils.data.DataLoader(data_bd_testset, batch_size=args.batch_size, num_workers=args.num_workers,drop_last=False, shuffle=True,pin_memory=True)

    asr_acc = 0
    for i, (inputs,labels) in enumerate(data_bd_loader):  # type: ignore
        inputs, labels = inputs.to(args.device), labels.to(args.device)
        outputs = result_defense['model'](inputs)
        pre_label = torch.max(outputs,dim=1)[1]
        asr_acc += torch.sum(pre_label == labels)
    asr_acc = asr_acc/len(data_bd_test)

    tran = get_transform(args.dataset, *([args.input_height,args.input_width]) , train = False)
    x = result['clean_test']['x']
    y = result['clean_test']['y']
    data_clean_test = list(zip(x,y))
    data_clean_testset = prepro_cls_DatasetBD(
        full_dataset_without_transform=data_clean_test,
        poison_idx=np.zeros(len(data_clean_test)),  # one-hot to determine which image may take bd_transform
        bd_image_pre_transform=None,
        bd_label_pre_transform=None,
        ori_image_transform_in_loading=tran,
        ori_label_transform_in_loading=None,
        add_details_in_preprocess=False,
    )
    data_clean_loader = torch.utils.data.DataLoader(data_clean_testset, batch_size=args.batch_size, num_workers=args.num_workers,drop_last=False, shuffle=True,pin_memory=True)

    clean_acc = 0
    for i, (inputs,labels) in enumerate(data_clean_loader):  # type: ignore
        inputs, labels = inputs.to(args.device), labels.to(args.device)
        outputs = result_defense['model'](inputs)
        pre_label = torch.max(outputs,dim=1)[1]
        clean_acc += torch.sum(pre_label == labels)
    clean_acc = clean_acc/len(data_clean_test)

    tran = get_transform(args.dataset, *([args.input_height,args.input_width]) , train = False)
    x = result['bd_test']['x']
    robust_acc = -1
    if 'original_targets' in result['bd_test']:
        y_ori = result['bd_test']['original_targets']
        if y_ori is not None:
            if len(y_ori) != len(x):
                y_idx = result['bd_test']['original_index']
                y = y_ori[y_idx]
            else :
                y = y_ori
            data_bd_test = list(zip(x,y))
            data_bd_testset = prepro_cls_DatasetBD(
                full_dataset_without_transform=data_bd_test,
                poison_idx=np.zeros(len(data_bd_test)),  # one-hot to determine which image may take bd_transform
                bd_image_pre_transform=None,
                bd_label_pre_transform=None,
                ori_image_transform_in_loading=tran,
                ori_label_transform_in_loading=None,
                add_details_in_preprocess=False,
            )
            data_bd_loader = torch.utils.data.DataLoader(data_bd_testset, batch_size=args.batch_size, num_workers=args.num_workers,drop_last=False, shuffle=True,pin_memory=True)
        
            robust_acc = 0
            for i, (inputs,labels) in enumerate(data_bd_loader):  # type: ignore
                inputs, labels = inputs.to(args.device), labels.to(args.device)
                outputs = result_defense['model'](inputs)
                pre_label = torch.max(outputs,dim=1)[1]
                robust_acc += torch.sum(pre_label == labels)
            robust_acc = robust_acc/len(data_bd_test)


    if not (os.path.exists(os.getcwd() + f'{save_path}/dbd/')):
        os.makedirs(os.getcwd() + f'{save_path}/dbd/')
    torch.save(
    {
        'model_name':args.model,
        'model': result_defense['model'].cpu().state_dict(),
        'asr': asr_acc,
        'acc': clean_acc,
        'ra': robust_acc
    },
    f'./{save_path}/dbd/defense_result.pt'
    )