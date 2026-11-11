#!/usr/bin/env python3
import os
import sys
import argparse
import fnmatch
import re

PROCESS_BY_SUBFOLDER = True
MIN_FOLDER_SIZE_KB = 20
MAX_SPLIT_DEPTH = 2
MAX_FILE_COUNT = 8

ALLOWED_ENDINGS = (
    '.md', '.txt', '.json', '.yaml', '.yml', '.gitignore',
    '.cs', '.csproj', '.sln', '.py', '.mcfunction',
    '.targets', '.ps1', '.bash', '.bat', '.sh',
    '.html', '.js', '.css', '.jsx', '.ts', '.tsx', '.astro',
    '.axaml', '.axaml.cs', '.xml',
    '.cpp', '.h', '.hpp', '.c', '.cc',
    '.cu', '.cuh', '.slang', '.cmake', '.log'
)
EXCLUDED_ENDINGS = (
    'Effects.prefab', 'UI.prefab', 'InputSystem_Actions.cs', 'Input_Riversports.cs',
    'packages-lock.json', 'package-lock.json',
    'SplatData.ts', 'SplatData.js', 'splat_data.js', 'test01_clean.mcfunction'
)
EXCLUDED_FOLDERS = (
    '.git', '.vs',
    'Library', 'Logs', 'ProjectSettings', 'Builds', 'Temp', 'obj', 'bin',
    'Controls', 'reference', 'build', 'build_cuda', 'build_vulkan', 'build_linux',
    'node_modules', '.next', 'dist'
)
FORCE_INCLUDE_FILES = {
    'volstream_crash.log',
    'volstream_startup.log',
    'OrbbecSDK.log.txt',
    '.gitignore'
}
MAX_SIZE_MB = 100
OUTPUT_EXT = '.xml'
SPLIT_KEYWORD = 'axaml'

def get_ignore_rules(start_path):
    rules = []
    for root, dirs, files in os.walk(start_path):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_FOLDERS]
        if '.gitignore' in files:
            with open(os.path.join(root, '.gitignore'), 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        is_dir_only = line.endswith('/')
                        clean_pat = line.rstrip('/')
                        rules.append((root, clean_pat, is_dir_only))
    return rules

def is_ignored(file_path, rules, is_dir=False):
    base_name = os.path.basename(file_path)
    if not is_dir and base_name in FORCE_INCLUDE_FILES:
        return False

    file_path_posix = file_path.replace(os.sep, '/')
    for rule_root, pattern, is_dir_only in rules:
        rule_root_posix = rule_root.replace(os.sep, '/')
        if file_path_posix.startswith(rule_root_posix):
            rel_path = file_path_posix[len(rule_root_posix):].lstrip('/')
            if not rel_path:
                continue
            parts = rel_path.split('/')
            if is_dir_only:
                dir_parts = parts if is_dir else parts[:-1]
                if any(fnmatch.fnmatch(part, pattern) for part in dir_parts):
                    return True
            else:
                if any(fnmatch.fnmatch(part, pattern) for part in parts) or fnmatch.fnmatch(rel_path, pattern):
                    return True
    return False

def write_project_output(target_path, files, base_dir):
    with open(target_path, 'w', encoding='utf-8') as outfile:
        outfile.write("<repository>\n<summary>\n")
        outfile.write("This file is a merged representation of the codebase, combined into a single document.\n\n")
        outfile.write("</summary>\n\n<project_structure>\n")
        for file_path in files:
            rel_path = os.path.relpath(file_path, base_dir).replace(os.sep, '/')
            outfile.write(f"{rel_path}\n")
        outfile.write("</project_structure>\n\n<files>\n")
        
        for file_path in files:
            rel_path = os.path.relpath(file_path, base_dir).replace(os.sep, '/')
            try:
                with open(file_path, 'r', encoding='utf-8-sig', errors='replace') as infile:
                    content = infile.read()
                
                content = content.replace('    ', '\t')
                
                if file_path.endswith('.cs'):
                    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
                    lines = []
                    for line in content.splitlines():
                        line = re.sub(r'//.*', '', line)
                        s_line = line.strip().lstrip('\ufeff')
                        if not s_line:
                            continue
                        if re.match(r'^(global\s+)?using\s+[^;]+;$', s_line) and '(' not in s_line:
                            continue
                        lines.append(line.rstrip())
                    content = '\n'.join(lines)
                else:
                    if file_path.endswith(('.axaml', '.xml')):
                        content = re.sub(r'>\s+<', '><', content)
                    content = '\n'.join(line.rstrip() for line in content.splitlines() if line.strip())
                
                outfile.write(f'<file path="{rel_path}">\n<![CDATA[\n{content}')
                if not content.endswith('\n'):
                    outfile.write('\n')
                outfile.write("]]>\n</file>\n\n")
            except Exception as e:
                print(f"Error reading {file_path}: {e}")
                
        outfile.write("</files>\n</repository>\n")

def get_valid_files(target_dir, rules, excluded_names, is_root_only=False):
    valid_files = []
    skipped_large_files = []
    
    for root, dirs, files in os.walk(target_dir):
        if is_root_only and root != target_dir:
            dirs[:] = []
            continue

        dirs[:] = [d for d in dirs if d not in EXCLUDED_FOLDERS and not is_ignored(os.path.join(root, d), rules)]
        
        for file in files:
            if file in excluded_names:
                continue
                
            file_path = os.path.join(root, file)
            
            if is_ignored(file_path, rules) or file.endswith(EXCLUDED_ENDINGS) or not file.endswith(ALLOWED_ENDINGS):
                continue
                
            size = os.path.getsize(file_path)
            if size > MAX_SIZE_MB * 1024 * 1024:
                skipped_large_files.append((file_path, size))
                continue
                
            valid_files.append(file_path)

    return valid_files, skipped_large_files

def process_and_write(valid_files, skipped_large_files, output_base_name, base_dir):
    if not valid_files:
        return

    valid_files.sort(key=lambda x: x.replace('.', '~'))

    main_files = []
    split_files = []

    for f in valid_files:
        if SPLIT_KEYWORD and SPLIT_KEYWORD.lower() in os.path.basename(f).lower():
            split_files.append(f)
        else:
            main_files.append(f)

    main_out_name = f"{output_base_name}{OUTPUT_EXT}"
    split_out_name = f"{output_base_name}_{SPLIT_KEYWORD}{OUTPUT_EXT}" if SPLIT_KEYWORD else None

    if main_files:
        write_project_output(os.path.join(base_dir, main_out_name), main_files, base_dir)

    if split_out_name and split_files:
        write_project_output(os.path.join(base_dir, split_out_name), split_files, base_dir)

    all_rel_paths = [os.path.relpath(f, base_dir) for f in valid_files] + \
                    [os.path.relpath(f, base_dir) for f, _ in skipped_large_files]
    max_path_len = max([len(p) for p in all_rel_paths]) if all_rel_paths else 0

    print(f"\n--- Processed: {output_base_name} ---")
    for f in main_files:
        print(f"Added [{main_out_name}]:       {os.path.relpath(f, base_dir):<{max_path_len}} | Size: {os.path.getsize(f)} bytes")
        
    for f in split_files:
        print(f"Added [{split_out_name}]: {os.path.relpath(f, base_dir):<{max_path_len}} | Size: {os.path.getsize(f)} bytes")
        
    if skipped_large_files:
        print("\n--- WARNING: Skipped Large Files ---")
        for f, size in skipped_large_files:
            print(f"Skipped: {os.path.relpath(f, base_dir):<{max_path_len}} | Size: {size} bytes")

def process_directory_level(current_dir, out_name, target_dir, rules, excluded_names, current_depth):
    if current_depth >= MAX_SPLIT_DEPTH:
        valid, skipped = get_valid_files(current_dir, rules, excluded_names)
        process_and_write(valid, skipped, out_name, target_dir)
        return

    local_valid, local_skipped = get_valid_files(current_dir, rules, excluded_names, is_root_only=True)

    for item in os.listdir(current_dir):
        item_path = os.path.join(current_dir, item)
        if os.path.isdir(item_path):
            if item not in EXCLUDED_FOLDERS and not is_ignored(item_path, rules):
                sub_valid, sub_skipped = get_valid_files(item_path, rules, excluded_names)
                folder_size = sum(os.path.getsize(f) for f in sub_valid)

                if folder_size < MIN_FOLDER_SIZE_KB * 1024:
                    local_valid.extend(sub_valid)
                    local_skipped.extend(sub_skipped)
                else:
                    sub_out_name = f"{out_name}_{item}" if current_depth > 0 else item
                    process_directory_level(item_path, sub_out_name, target_dir, rules, excluded_names, current_depth + 1)

    if local_valid:
        process_and_write(local_valid, local_skipped, out_name, target_dir)

def collect_folder_chunks(current_dir, out_name, rules, excluded_names, current_depth):
    chunks = []
    local_valid, local_skipped = get_valid_files(current_dir, rules, excluded_names, is_root_only=True)

    if current_depth >= MAX_SPLIT_DEPTH:
        valid, skipped = get_valid_files(current_dir, rules, excluded_names)
        return [{"name": out_name, "valid": valid, "skipped": skipped}]

    for item in os.listdir(current_dir):
        item_path = os.path.join(current_dir, item)
        if os.path.isdir(item_path):
            if item not in EXCLUDED_FOLDERS and not is_ignored(item_path, rules):
                sub_valid, sub_skipped = get_valid_files(item_path, rules, excluded_names)
                folder_size = sum(os.path.getsize(f) for f in sub_valid)

                if folder_size < MIN_FOLDER_SIZE_KB * 1024:
                    local_valid.extend(sub_valid)
                    local_skipped.extend(sub_skipped)
                else:
                    sub_out_name = f"{out_name}_{item}" if current_depth > 0 else item
                    chunks.extend(collect_folder_chunks(item_path, sub_out_name, rules, excluded_names, current_depth + 1))

    if local_valid:
        chunks.append({"name": out_name, "valid": local_valid, "skipped": local_skipped})

    return chunks

def balance_and_merge_chunks(chunks, max_count):
    if not chunks or max_count <= 0 or len(chunks) <= max_count:
        return chunks

    for chunk in chunks:
        chunk["size"] = sum(os.path.getsize(f) for f in chunk["valid"])

    while len(chunks) > max_count:
        chunks.sort(key=lambda c: c["size"])
        c1 = chunks.pop(0)
        c2 = chunks.pop(0)

        merged = {
            "name": f"{c1['name']}+{c2['name']}",
            "valid": c1["valid"] + c2["valid"],
            "skipped": c1["skipped"] + c2["skipped"],
            "size": c1["size"] + c2["size"]
        }
        chunks.append(merged)

    return chunks

def main():
    parser = argparse.ArgumentParser(description="Combine codebase into XML files.")
    parser.add_argument("target_dir", nargs="?", default=os.getcwd(), help="Target directory to process (defaults to current working directory)")
    parser.add_argument("--max-files", type=int, default=MAX_FILE_COUNT, help="Maximum number of XML file sets to produce")
    parser.add_argument("--no-pause", action="store_true", help="Skip the pause at the end (useful for CI/CLI usage)")
    args = parser.parse_args()

    target_dir = os.path.abspath(args.target_dir)
    script_name = os.path.basename(__file__)
    
    base_name = os.path.basename(target_dir)
    if not base_name:
        base_name = "combined_project"

    rules = get_ignore_rules(target_dir)
    print(f"Processing directory: {target_dir}")

    if PROCESS_BY_SUBFOLDER:
        chunks = collect_folder_chunks(target_dir, base_name, rules, {script_name}, 0)
        balanced_chunks = balance_and_merge_chunks(chunks, args.max_files)
        for chunk in balanced_chunks:
            process_and_write(chunk["valid"], chunk["skipped"], chunk["name"], target_dir)
    else:
        valid_files, skipped_large_files = get_valid_files(target_dir, rules, {script_name})
        process_and_write(valid_files, skipped_large_files, base_name, target_dir)

    print("\nProcess complete.")
    if not args.no_pause:
        input("Press Enter to exit...")

if __name__ == '__main__':
    main()