import os
import re

def process_tex_file(filepath):
    """
    处理单个 .tex 文件，查找并替换题目格式。
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # --- 1. 定义多子问题大题的正则表达式和替换函数 (已修改) ---
    # 旧的正则表达式过于复杂，对每个子问题的结尾都进行判断，容易因格式不规范而出错。
    # 新的正则表达式采用更稳健的策略：
    # 1. 匹配主问题行。
    # 2. 匹配一个必须以子问题“（数字）”开头的文本块。
    # 3. 使用前瞻（lookahead）来确定这个文本块的结束位置，即下一个主问题或文件末尾。
    # 这样大大简化了模式，并能更好地处理包含2个、3个或更多子问题的各种情况。
    multi_part_pattern = re.compile(
        # Group 1: 大题题号, Group 2: 大题题干
        r'^\s*(\d+)[．.]\s*(.*?)\s*\n' +

        # Group 3: 捕获整个子问题块。
        r'(' +
            # 子问题1 (必须存在，单行)
            r'^\s*(?:（1）|1\.)[^\n]*\n' +
            # 子问题2 (必须存在，单行)
            # 使用 `[^\n\r]*` 来匹配到行尾，兼容不同系统的换行符
            r'^\s*(?:（2）|2\.)[^\n\r]*' +
            # 匹配所有后续的、连续的、格式正确的单行子问题
            r'(?:' +
                r'\n^\s*(?:（\d+）|\d+\.)[^\n\r]*' +
            r')*' +
        r')',
        re.MULTILINE
    )

    def replacement_func_multi_part(match):
        main_stem = match.group(2).strip() # 大题题干
        sub_questions_block = match.group(3).strip() # 整个子问题块

        # 子问题解析器：现在可以同时处理“（1）”和“1.”两种格式
        # 这个解析器无需修改，因为它能很好地处理传入的、边界正确的文本块。
        sub_q_parser_pattern = re.compile(
            # 使用 ^ 和 MULTILINE 来匹配块中每一行的开头
            r'^\s*(?:（(\d+)）|(\d+)\.)\s*([\s\S]*?)(?=\n\s*(?:（\d+）|\d+\.)|\Z)',
            re.MULTILINE
        )

        sub_q_matches = sub_q_parser_pattern.finditer(sub_questions_block)
        
        enumerate_content = []
        for sub_match in sub_q_matches:
            # 子问题内容总是最后一个捕获组 (Group 3)
            sub_q_content = sub_match.group(3).strip()
            if sub_q_content: # 确保内容不为空
                enumerate_content.append(f'    \\item {sub_q_content}')

        if not enumerate_content:
            return match.group(0)

        new_format = (
            f'\n\\begin{{example}}\n'
            f'{main_stem}\n'
            '\\begin{enumerate}\n'
            f'{os.linesep.join(enumerate_content)}\n'
            '\\end{enumerate}\n'
            '\\end{example}'
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
            f'\n\\begin{{example}}\n'
            f'{stem} （\\qquad）\n'
            '\\begin{tasks}(4)\n'
            f'    \\task {option_a}\n'
            f'    \\task {option_b}\n'
            f'    \\task {option_c}\n'
            f'    \\task {option_d}\n'
            '\\end{tasks}\n'
            '\\end{example}'
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