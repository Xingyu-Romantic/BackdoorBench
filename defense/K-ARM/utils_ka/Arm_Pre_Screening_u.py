# Arm pre-screening
##############################################################################################################################
### Pre_Screening function will scan potential global trigger and label specific trigger in order.                         ###
### If a potential global trigger is found, it will return the target label and stop scanning label specific trigger       ###
### If a potential label specific trigger is found, it will return the target-victim label pair                            ###
##############################################################################################################################


import torch 
from torchvision import transforms
#from dataset import CustomDataSet
from torch.utils.data import DataLoader
import torch.nn.functional as F
import numpy as np

import os 
import sys

from utils.aggregate_block.dataset_and_transform_generate import get_transform
from utils.bd_dataset import prepro_cls_DatasetBD
from utils.nCHW_nHWC import nCHW_to_nHWC
sys.path.append(os.getcwd())



def Pre_Screening(args,model,result):
    device = torch.device(args.device)
    transform = get_transform(args.dataset, *([args.input_height,args.input_width]) , train = True)
    # transform = transforms.Compose([
    #     transforms.CenterCrop(args.input_width),
    #     transforms.ToTensor()
    #     ])
    x = torch.tensor(nCHW_to_nHWC(result['bd_train']['x'].numpy()))
    y = result['bd_train']['y']
    data_set = torch.utils.data.TensorDataset(x,y)
    dataset = prepro_cls_DatasetBD(
        full_dataset_without_transform=data_set,
        poison_idx=np.zeros(len(data_set)),  # one-hot to determine which image may take bd_transform
        bd_image_pre_transform=None,
        bd_label_pre_transform=None,
        ori_image_transform_in_loading=transform,
        ori_label_transform_in_loading=None,
        add_details_in_preprocess=False,
    )
    data_loader = torch.utils.data.DataLoader(dataset, batch_size=args.batch_size, num_workers=args.num_workers,drop_last=False, shuffle=True,pin_memory=True)
    acc = 0
    for idx, (img,label) in enumerate(data_loader):
        img,label = img.to(device),label.to(device)
        #img = img[:,permute,:,:]
        output = model(img)
        logits = F.softmax(output,1)
        _,pred = torch.max(output,1)
        if idx == 0:
            logits_all = logits.detach().cpu()
            preds_all = pred.detach().cpu()
        else:
            logits_all = torch.cat((logits_all,logits.detach().cpu()),0)
            preds_all = torch.cat((preds_all,pred.detach().cpu()),0)

        acc +=  pred.eq(label.long().view_as(pred)).sum().item()


    if args.num_classes <= 8:
        k = 2
    else:
        k = round(args.num_classes * args.gamma)

    topk_index = torch.topk(logits_all,k,dim=1)[1]
    topk_logit = torch.topk(logits_all,k,dim=1)[0]


    # step 1: check all label trigger
    target_label = all_label_trigger_det(args,topk_index)



    if target_label != -1:
        return target_label,None
    else:
        target_matrix,median_matrix = specific_label_trigger_det(args,topk_index,topk_logit)
        target_class_all = []
        triggered_classes_all = []
        for i in range(target_matrix.size(0)):
            if target_matrix[i].max() > 0:
                target_class = i
                triggered_classes = (target_matrix[i]).nonzero().view(-1)
                triggered_classes_logits = target_matrix[i][target_matrix[i]>0]
                triggered_classes_medians = median_matrix[i][target_matrix[i]>0]
             
 
                top_index_logit = (triggered_classes_logits > 1e-08).nonzero()[:,0]
                top_index_median = (triggered_classes_medians > 1e-08).nonzero()[:,0]
                
                top_index = torch.LongTensor(np.intersect1d(top_index_logit, top_index_median))


                if len(top_index) > 0:
                    triggered_classes = triggered_classes[top_index]

                    triggered_classes_logits = triggered_classes_logits[top_index]
 
                    if triggered_classes.size(0) > 3:
                        top_3_index = torch.topk(triggered_classes_logits,3,dim=0)[1]
                        triggered_classes = triggered_classes[top_3_index]
                    
                    target_class_all.append(target_class)
                    triggered_classes_all.append(triggered_classes)
        
        return target_class_all, triggered_classes_all




def all_label_trigger_det(args,topk_index):

    target_label = -1
    count_all = torch.zeros(args.num_classes)
    for i in range(args.num_classes):
        count_all[i] = topk_index[topk_index == i].size(0)
    max_count = torch.max(count_all)
    max_index = torch.argmax(count_all)
    if max_count > args.global_theta * topk_index.size(0):

        target_label = max_index
    return target_label




def specific_label_trigger_det(args,topk_index,topk_logit):
    sum_mat = torch.zeros(args.num_classes,args.num_classes)
    median_mat = torch.zeros(args.num_classes,args.num_classes)
    #print('topk_index:',topk_index.size())
    #print('topk_logit:',topk_logit.size())
    #print('==========')


    for i in range(args.num_classes):  
        #for each class, find the index of samples belongs to that class tmp_1 => index, tmp_1_logit => corresponding logit
        tmp_1 = topk_index[topk_index[:,0] == i]
        #print(tmp_1)

        tmp_1_logit = topk_logit[topk_index[:,0] == i]
        #print(tmp_1_logit)
        tmp_2 = torch.zeros(args.num_classes)
        for j in range(args.num_classes):
            # for every other class, 
            if j == i:
                tmp_2[j] = -1
            else:
                tmp_2[j] = tmp_1[tmp_1 == j].size(0) / tmp_1.size(0)

                #if tmp_2[j]  == 1:
                if tmp_2[j]  >= args.local_theta:
                    
                    sum_var =  tmp_1_logit[tmp_1 == j].sum()
                    median_var = torch.median(tmp_1_logit[tmp_1 == j])
                    sum_mat[j,i] = sum_var
                    median_mat[j,i] = median_var
                    #print('Potential Target:{0}, Potential Victim:{1}, Ratio:{2}, Logits Sum:{3}, Logits Median:{4}'.format(j,i,tmp_2[j],sum_var,median_var))
                    #print('Potential victim: '+ str(i) + ' Potential target:' + str(j) + ' Ratio: ' + str(tmp_2[j]) + ' Logits Mean: '+ str(mean_var) + ' Logits std: ' + str(std_var) + 'Logit Median: ' + str(median_var))
    return sum_mat, median_mat