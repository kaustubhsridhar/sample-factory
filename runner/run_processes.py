"""Run many experiments, hyperparameter sweeps, etc."""

import os
import subprocess
import time
from os.path import join

from utils.utils import log, experiment_dir


def run(run_description, args):
    experiments = run_description.experiments
    max_parallel = args.max_parallel

    log.info('Starting processes with base cmds: %r', [e.cmd for e in experiments])
    log.info('Max parallel processes is %d', max_parallel)
    log.info('Monitor log files using\n\n\ttail -f train_dir/%s/**/**/log.txt\n\n', run_description.run_name)

    processes = []
    processes_per_gpu = {g: [] for g in range(args.num_gpus)}

    experiments = run_description.generate_experiments()
    next_experiment = next(experiments, None)

    def find_least_busy_gpu():
        least_busy_gpu = None
        gpu_available_processes = 0

        for gpu_id in range(args.num_gpus):
            available_processes = args.experiments_per_gpu - len(processes_per_gpu[gpu_id])
            if available_processes > gpu_available_processes:
                gpu_available_processes = available_processes
                least_busy_gpu = gpu_id

        return least_busy_gpu, gpu_available_processes

    def can_squeeze_another_process():
        if len(processes) >= max_parallel:
            return False

        if args.experiments_per_gpu > 0:
            least_busy_gpu, gpu_available_processes = find_least_busy_gpu()
            if gpu_available_processes <= 0:
                return False

        return True

    while len(processes) > 0 or next_experiment is not None:
        while can_squeeze_another_process() and next_experiment is not None:
            cmd, name, root_dir, exp_env_vars = next_experiment
            cmd_tokens = cmd.split(' ')

            logfile = open(join(experiment_dir(name, root_dir), 'log.txt'), 'wb')
            envvars = os.environ.copy()

            best_gpu = None
            if args.experiments_per_gpu > 0:
                best_gpu, best_gpu_available_processes = find_least_busy_gpu()
                log.info(
                    'The least busy gpu is %d where we can run %d more processes',
                    best_gpu, best_gpu_available_processes,
                )
                envvars['CUDA_VISIBLE_DEVICES'] = f'{best_gpu}'

            log.info('Starting process %s', cmd)

            if exp_env_vars is not None:
                for key, value in exp_env_vars.items():
                    log.info('Adding env variable %r %r', key, value)
                    envvars[str(key)] = str(value)

            process = subprocess.Popen(cmd_tokens, stdout=logfile, stderr=logfile, env=envvars)
            process.process_logfile = logfile
            process.gpu_id = -1 if best_gpu is None else best_gpu
            process.proc_cmd = cmd

            processes.append(process)

            if process.gpu_id > 0:
                processes_per_gpu[process.gpu_id].append(process.proc_cmd)

            log.info('Started process %s on GPU %d', process.proc_cmd, process.gpu_id)
            log.info('Waiting for %d seconds before starting next process', args.pause_between)
            time.sleep(args.pause_between)

            next_experiment = next(experiments, None)

        remaining_processes = []
        for process in processes:
            if process.poll() is None:
                remaining_processes.append(process)
                continue
            else:
                if process.gpu_id > 0:
                    processes_per_gpu[process.gpu_id].remove(process.proc_cmd)
                process.process_logfile.close()
                log.info('Process %r finished with code %r', process.proc_cmd, process.returncode)
                if process.returncode != 0:
                    log.error('WARNING: RETURN CODE IS %r', process.returncode)

        processes = remaining_processes
        time.sleep(0.1)

    log.info('Done!')

    return 0
