'''
我们测试无论是什么的准确性其实就是一个数值，表示一个映射在实验中达成的数量是多少。
限于trainer写的方式，我这边先不讨论random的情况，如果一个攻击样本我们希望output随机那就只有另写。
这里的pidx用来测试的只能是固定且唯一的。
一般用generate_pidx_from_label_transform就行
'''
import sys, logging
sys.path.append('../')
import random
import numpy as np
from typing import Callable, Union, List

def generate_single_target_attack_train_pidx(
        targets:Union[np.ndarray, List],
        tlabel: int,
        pratio: Union[float, None] = None,
        p_num: Union[int,None] = None,
        clean_label: bool = False,
) -> np.ndarray:
    '''
    idea: avoid  sample with target label ( since cannot infer wheather attack succeed)
    '''
    logging.info('Reminder: plz note that if p_num or pratio exceed the number of possible candidate samples\n then only maximum number of samples will be applied')
    logging.info('Reminder: priority p_num > pratio, and choosing fix number of sample is prefered if possible ')
    pidx = np.zeros(len(targets))
    if clean_label == False:
        if p_num is not None or round(pratio * len(targets)):
            if p_num is not None:
                non_zero_array = np.random.choice(np.where(targets != tlabel)[0], p_num, replace = False)
                pidx[list(non_zero_array)] = 1
            else:
                non_zero_array = np.random.choice(np.where(targets != tlabel)[0], round(pratio * len(targets)), replace = False)
                pidx[list(non_zero_array)] = 1
    else:
        if p_num is not None or round(pratio * len(targets)):
            if p_num is not None:
                non_zero_array = np.random.choice(np.where(targets == tlabel)[0], p_num, replace = False)
                pidx[list(non_zero_array)] = 1
            else:
                non_zero_array = np.random.choice(np.where(targets == tlabel)[0], round(pratio * len(targets)), replace = False)
                pidx[list(non_zero_array)] = 1
    logging.info(f'poison num:{sum(pidx)},real pratio:{sum(pidx) / len(pidx)}')
    if sum(pidx) == 0:
        raise SystemExit('No poison sample generated !')
    return pidx

def generate_multi_target_attack_train_pidx(
        targets:Union[np.ndarray, List],
        tlabel_list:List,
        pratio: Union[float, None] = None,
        p_num: Union[int,None] = None,
) -> np.ndarray:
    '''
    idea: avoid  sample with target label ( since cannot infer wheather attack succeed)

    '''
    logging.info('Reminder: plz note that if p_num or pratio exceed the number of possible candidate samples\n then only maximum number of samples will be applied')
    logging.info('Reminder: priority p_num > pratio, and choosing fix number of sample is prefered if possible ')
    pidx = np.zeros(len(targets))
    if p_num is not None or round(pratio * len(targets)):
        if p_num is not None:
            non_zero_array = np.random.choice(np.where([True if i not in tlabel_list else False for i in targets ])[0], p_num, replace = False)
            pidx[list(non_zero_array)] = 1
        else:
            non_zero_array = np.random.choice(np.where([True if i not in tlabel_list else False for i in targets ])[0], round(pratio * len(targets)), replace = False)
            pidx[list(non_zero_array)] = 1
    # else:
    #     for (i, t) in enumerate(targets):
    #         if random.random() < pratio and t not in tlabel_list:
    #             pidx[i] = 1
    logging.info(f'poison num:{sum(pidx)},real pratio:{sum(pidx) / len(pidx)}')
    if sum(pidx) == 0:
        raise SystemExit('No poison sample generated !')
    return pidx

def generate_pidx_from_label_transform(
        original_labels: Union[np.ndarray, List],
       label_transform:Callable,
        is_train: bool,
        pratio : Union[float,None] = None,
        p_num: Union[int,None] = None,
) -> np.ndarray:
    '''
    idea: avoid sample with target label ( since cannot infer wheather attack succeed)
    !only support label_transform with deterministic output value (one sample one fix target label)!
    '''
    logging.info(f'Reminder: generate_pidx_from_label_transform only support attack that one sample has one fix target label')
    logging.info('Reminder: plz note that if p_num or pratio exceed the number of possible candidate samples\n then only maximum number of samples will be applied')
    logging.info('Reminder: priority p_num > pratio, and choosing fix number of sample is prefered if possible ')
    pidx = np.zeros_like(original_labels)
    original_labels = np.array(original_labels)
    labels_after_transform = np.array( [label_transform(label) for label in original_labels] )
    label_change_idx = np.where(original_labels!=labels_after_transform)[0]
    if not is_train:
        logging.info('pratio does not apply during test phase')
        pidx[list(label_change_idx)] = 1
    else:
        if p_num is not None or round(pratio * len(original_labels)):
            if p_num is not None:
                non_zero_array = np.random.choice(label_change_idx, p_num, replace = False)
                pidx[list(non_zero_array)] = 1
            else:
                non_zero_array = np.random.choice(label_change_idx,
                                        round(pratio * len(original_labels)), replace = False)
                pidx[list(non_zero_array)] = 1
        # else:
        #     for idx in label_change_idx:
        #         if random.random() < pratio:
        #             pidx[idx] = 1
    logging.info(f'poison num:{sum(pidx)},real pratio:{sum(pidx) / len(pidx)}')
    if sum(pidx) == 0:
        raise SystemExit('No poison sample generated !')
    return pidx

if __name__ == '__main__':
    from bd_label_transform.backdoor_label_transform import OneToOne_attack
    label = np.array([1,2,3,1,2])
    label_transform = OneToOne_attack(1,3)
    print(generate_single_target_attack_train_pidx(label, 1, 1, 1))
    print(generate_multi_target_attack_train_pidx(label, [1,5,6], 1, 1))
    print(generate_pidx_from_label_transform(label, OneToOne_attack(1,9), True, 0.5, None))