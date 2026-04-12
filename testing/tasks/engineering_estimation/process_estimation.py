#!/usr/bin/env python3
import sqlite3
import re
import pandas as pd
from pathlib import Path

def parse_material_item(item):
    """解析单个材料项，返回材料类型、规格、成分和数量"""
    # 移除空格
    item = item.strip()
    
    # 匹配数量和单位
    # 尝试匹配吨、米等单位
    quantity_match = re.search(r'([\d\.]+)\s*(吨|米)', item)
    if not quantity_match:
        return None
    
    quantity = float(quantity_match.group(1))
    unit = quantity_match.group(2)
    
    # 提取材料描述部分（去掉数量部分）
    description = item[:quantity_match.start()].strip()
    
    # 尝试匹配常见的材料类型
    material_types = [
        '角钢', '槽钢', '螺纹钢', '钢管', 'H型钢', '给水管', '电缆', 
        '铝合金型材', '工字钢', '圆钢', '线缆', '铝型材'
    ]
    
    material = None
    for mt in material_types:
        if mt in description:
            material = mt
            break
    
    # 如果没有匹配到，尝试其他匹配
    if not material:
        if '型钢' in description:
            material = '型钢'
        elif '管' in description:
            material = '管材'
        elif '钢' in description:
            material = '钢材'
        elif '电缆' in description or '线缆' in description:
            material = '电缆'
        elif '型材' in description:
            material = '型材'
    
    # 提取规格和成分
    # 规格通常是尺寸信息
    dimension = None
    composition = None
    
    # 尝试提取尺寸信息
    dimension_patterns = [
        r'L\d+×\d+mm',  # L40×4mm
        r'\d+×\d+×\d+\.?\d*mm',  # 160×63×6.5mm
        r'HRB\d+[A-Z]*\s*Φ\d+mm',  # HRB400E Φ12mm
        r'DN\d+×\d+\.?\d*mm',  # DN25×3.25mm
        r'De\d+×\d+\.?\d*mm',  # De63×4.7mm
        r'YJV\s*\d+×\d+mm²',  # YJV 4×25mm²
        r'\d+×\d+×\d+\.?\d*×\d+\.?\d*mm',  # 350×175×7×11mm
        r'I\d+\s*\d+×\d+×\d+\.?\d*mm',  # I18 180×94×6.5mm
        r'Φ\d+mm×\d+m',  # Φ10mm×9m
    ]
    
    for pattern in dimension_patterns:
        match = re.search(pattern, description)
        if match:
            dimension = match.group(0)
            break
    
    # 成分通常是材料后面的描述
    if dimension:
        # 从描述中移除规格部分得到成分
        comp_part = description.replace(dimension, '').strip()
        # 清理多余的空格和标点
        composition = re.sub(r'[，,、\s]+$', '', comp_part)
        composition = re.sub(r'^[的尺寸]*', '', composition)
    
    return {
        'material': material,
        'dimension': dimension,
        'composition': composition,
        'quantity': quantity,
        'unit': unit,
        'original': item
    }

def match_material_price(db_conn, material_info):
    """在数据库中匹配材料价格"""
    cursor = db_conn.cursor()
    
    # 尝试多种匹配策略
    matches = []
    
    # 策略1：完全匹配材料、规格和成分
    if material_info['material'] and material_info['dimension'] and material_info['composition']:
        cursor.execute('''
            SELECT material, dimension, composition, unit_price 
            FROM material_price 
            WHERE material LIKE ? 
            AND dimension LIKE ? 
            AND composition LIKE ?
        ''', (
            f"%{material_info['material']}%",
            f"%{material_info['dimension']}%",
            f"%{material_info['composition']}%"
        ))
        matches.extend(cursor.fetchall())
    
    # 策略2：匹配规格和成分
    if material_info['dimension'] and material_info['composition'] and not matches:
        cursor.execute('''
            SELECT material, dimension, composition, unit_price 
            FROM material_price 
            WHERE dimension LIKE ? 
            AND composition LIKE ?
        ''', (
            f"%{material_info['dimension']}%",
            f"%{material_info['composition']}%"
        ))
        matches.extend(cursor.fetchall())
    
    # 策略3：只匹配规格
    if material_info['dimension'] and not matches:
        cursor.execute('''
            SELECT material, dimension, composition, unit_price 
            FROM material_price 
            WHERE dimension LIKE ?
        ''', (f"%{material_info['dimension']}%",))
        matches.extend(cursor.fetchall())
    
    # 策略4：匹配材料类型和成分
    if material_info['material'] and material_info['composition'] and not matches:
        cursor.execute('''
            SELECT material, dimension, composition, unit_price 
            FROM material_price 
            WHERE material LIKE ? 
            AND composition LIKE ?
        ''', (
            f"%{material_info['material']}%",
            f"%{material_info['composition']}%"
        ))
        matches.extend(cursor.fetchall())
    
    return matches

def parse_unit_price(price_str):
    """解析单价字符串，返回数值和单位"""
    if not price_str:
        return None, None
    
    # 匹配数字部分
    match = re.search(r'([\d\.]+)', price_str)
    if not match:
        return None, None
    
    value = float(match.group(1))
    
    # 提取单位
    unit = None
    if '元/吨' in price_str:
        unit = '吨'
    elif '元/米' in price_str:
        unit = '米'
    
    return value, unit

def calculate_total_price(quantity, unit, unit_price_value, unit_price_unit):
    """计算总价"""
    if not unit_price_value or not unit_price_unit:
        return None
    
    # 检查单位是否匹配
    if unit != unit_price_unit:
        # 尝试转换（这里简化处理，实际可能需要密度等转换）
        return None
    
    return quantity * unit_price_value

def main():
    # 文件路径
    source_file = Path(__file__).parent / 'source.txt'
    db_file = Path(__file__).parent / 'material_price.db'
    
    # 输出到runtime目录
    runtime_dir = Path(__file__).parent.parent / 'runtime'
    runtime_dir.mkdir(exist_ok=True)
    output_file = runtime_dir / '工程估价结果.xlsx'
    
    # 读取源文件
    with open(source_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 按行分割
    lines = content.strip().split('\n')
    
    # 连接数据库
    conn = sqlite3.connect(db_file)
    
    # 处理所有材料
    all_results = []
    unmatched_items = []
    
    for line_num, line in enumerate(lines, 1):
        # 按逗号分割每个材料项
        items = [item.strip() for item in line.split('，') if item.strip()]
        
        for item in items:
            # 解析材料项
            material_info = parse_material_item(item)
            if not material_info:
                unmatched_items.append({
                    'line': line_num,
                    'item': item,
                    'reason': '无法解析'
                })
                continue
            
            # 在数据库中匹配价格
            matches = match_material_price(conn, material_info)
            
            if not matches:
                unmatched_items.append({
                    'line': line_num,
                    'item': item,
                    'material_info': material_info,
                    'reason': '数据库中未找到匹配'
                })
                continue
            
            # 使用第一个匹配
            match = matches[0]
            material, dimension, composition, unit_price_str = match
            
            # 解析单价
            unit_price_value, unit_price_unit = parse_unit_price(unit_price_str)
            
            if not unit_price_value or not unit_price_unit:
                unmatched_items.append({
                    'line': line_num,
                    'item': item,
                    'material_info': material_info,
                    'reason': f'无法解析单价: {unit_price_str}'
                })
                continue
            
            # 计算总价
            total_price = calculate_total_price(
                material_info['quantity'],
                material_info['unit'],
                unit_price_value,
                unit_price_unit
            )
            
            if total_price is None:
                unmatched_items.append({
                    'line': line_num,
                    'item': item,
                    'material_info': material_info,
                    'reason': f'单位不匹配: {material_info["unit"]} vs {unit_price_unit}'
                })
                continue
            
            # 添加到结果
            all_results.append({
                '序号': len(all_results) + 1,
                '材料名称': material,
                '规格型号': dimension,
                '材料成分': composition,
                '数量': material_info['quantity'],
                '单位': material_info['unit'],
                '单价(元)': unit_price_value,
                '总价(元)': total_price,
                '原始描述': material_info['original']
            })
    
    # 关闭数据库连接
    conn.close()
    
    # 创建DataFrame
    if all_results:
        df = pd.DataFrame(all_results)
        
        # 计算所有有效材料的总价和
        total_sum = df['总价(元)'].sum()
        
        # 保存到Excel
        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='工程估价', index=False)
            
            # 添加汇总信息
            summary_df = pd.DataFrame({
                '项目': ['有效材料总数', '总价合计(元)'],
                '数值': [len(df), total_sum]
            })
            summary_df.to_excel(writer, sheet_name='汇总', index=False)
        
        print(f"处理完成！")
        print(f"成功匹配材料数量: {len(df)}")
        print(f"未匹配材料数量: {len(unmatched_items)}")
        print(f"总价合计: {total_sum:.2f}元")
        print(f"结果已保存到: {output_file}")
        
        # 输出未匹配的项目
        if unmatched_items:
            print("\n未匹配的材料项:")
            for item in unmatched_items:
                print(f"  行{item['line']}: {item['item']} - {item['reason']}")
    else:
        print("没有成功匹配到任何材料！")
        
        if unmatched_items:
            print("\n所有材料项都未匹配:")
            for item in unmatched_items:
                print(f"  行{item['line']}: {item['item']} - {item['reason']}")

if __name__ == '__main__':
    main()