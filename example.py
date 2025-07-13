import os
import re

def process_tex_file(filepath):
    """
    处理单个 .tex 文件，查找并替换题目格式。
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    multi_part_pattern = re.compile(
        r'^\s*(\d+)[．.]\s*(.*?)\s*\n' +
        # 匹配行首题号、题干直到第一个换行符 (Group 1: 题号, Group 2: 题干)
        # Group 3: 匹配从第一个子问题开始的整个子问题块，直到下一个主问题或文件结束
        r'(\s*（\d+）[\s\S]*?(?=\n\s*\d+[．.]|\Z))'
        , re.DOTALL | re.MULTILINE
        # DOTALL 让 . 匹配换行符，MULTILINE 让 ^ 和 $ 匹配行首行尾
    )

    def replacement_func_multi_part(match):
        main_stem = match.group(2).strip() # 主题干
        sub_questions_block = match.group(3) # 所有子问题组成的块

        # 用于解析子问题块内部的每个子问题
        # Group 1: 子问题序号 (e.g., '1', '2')
        # Group 2: 子问题内容
        sub_q_parser_pattern = re.compile(
            r'\s*（(\d+)）\s*([\s\S]*?)(?=\n\s*（\d+）|\Z)', # 匹配（数字）开头，直到下一个（数字）或块结束
            re.DOTALL
        )

        # 查找所有子问题
        sub_q_matches = sub_q_parser_pattern.finditer(sub_questions_block)
        
        # 构建 enumerate 环境内容
        enumerate_content = []
        for sub_match in sub_q_matches:
            # sub_q_num = sub_match.group(1) # 子问题序号，enumerate 会自动编号
            sub_q_content = sub_match.group(2).strip() # 子问题内容
            enumerate_content.append(f'    \\item {sub_q_content}')

        # 组合成新的格式
        new_format = (
            f'\n{main_stem}\n' # 只保留主题干，去除主问题号
            '\\begin{enumerate}\n'
            f'{os.linesep.join(enumerate_content)}\n' # 使用系统默认的换行符连接各项
            '\\end{enumerate}'
        )
        return new_format

    # --- 2. 定义单选题的正则表达式和替换函数 (已还原为用户提供的版本) ---
    single_choice_pattern = re.compile(
        r'^\s*(\d+)[．.]\s*(.*?)\s*（\s*\）\s*\n+'  # 行首可选空白，题号（捕获组1），中英文句号，可选空白，
                                                 # 题干（捕获组2），可选空白，匹配原题中的（ ）及其内部空白，至少一个换行符
        r'(?:\s*A[．.]\s*([\s\S]*?)\n)'           # 选项 A (捕获组3)，匹配内容直到换行符
        r'(?:\s*B[．.]\s*([\s\S]*?)\n)'           # 选项 B (捕获组4)
        r'(?:\s*C[．.]\s*([\s\S]*?)\n)'           # 选项 C (捕获组5)
        r'(?:\s*D[．.]\s*([\s\S]*?)\n)',          # 选项 D (捕获组6)，匹配内容直到换行符
        re.DOTALL | re.MULTILINE                  # DOTALL 使得 . 匹配换行，MULTILINE 使得 ^ 和 $ 匹配每一行的开头和结尾
    )

    def replacement_func_single_choice(match):
        problem_number = match.group(1)
        stem = match.group(2).strip()
        option_a = match.group(3).strip()
        option_b = match.group(4).strip()
        option_c = match.group(5).strip()
        option_d = match.group(6).strip()

        # 构建新的格式
        new_format = (
            f'\n{stem} （\\qquad）\n'
            '\\begin{tasks}(4)\n'
            f'    \\task {option_a}\n'  # 选项内容通常放在数学模式中
            f'    \\task {option_b}\n'
            f'    \\task {option_c}\n'
            f'    \\task {option_d}\n'
            '\\end{tasks}\n'
        )
        return new_format

    # --- 3. 应用替换规则 ---
    # 优先处理多子问题大题，因为其模式更具体
    processed_content = multi_part_pattern.sub(replacement_func_multi_part, content)
    # 然后处理单选题
    processed_content = single_choice_pattern.sub(replacement_func_single_choice, processed_content)

    # 如果内容有变化，则写入文件
    if processed_content != content:
        print(f"检测到并修改文件: {filepath}")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(processed_content)

def recursive_find_and_process(root_dir):
    """
    递归查找指定目录下的所有 .tex 文件并进行处理。
    """
    print(f"开始在目录 '{root_dir}' 中递归查找 .tex 文件...")
    for dirpath, _, filenames in os.walk(root_dir):
        for filename in filenames:
            if filename.endswith('.tex'):
                filepath = os.path.join(dirpath, filename)
                process_tex_file(filepath)
    print("所有 .tex 文件处理完毕。")

if __name__ == "__main__":
    target_directory = 'F:\\OneDrive\\Project\\LateX\\SeniorMath\\chapters'
    recursive_find_and_process(target_directory)