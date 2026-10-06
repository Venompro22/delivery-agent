# 📝 Honest exam: NEW wording, never seen in DATA (with traps like negation "没")
EVAL = [
    ("那就别送了", "cancel"), ("我改主意了 不需要了", "cancel"), ("先不用送 下次再说", "cancel"),
    ("订错了 请帮我撤销", "cancel"), ("今天的水别送了", "cancel"),
    ("还没收到", "question"), ("等了一个小时了 人呢", "question"), ("大概几点能送过来", "question"),
    ("现在送到哪一步了", "question"), ("快递员出发了吗", "question"),
    ("明天上午来三桶", "new_order"), ("帮我们办公室送五桶矿泉水", "new_order"), ("再来一桶 老地址", "new_order"),
    ("能送两箱水到图书馆吗", "new_order"), ("家里没水了 送一桶过来", "new_order"),
    ("货已经放他门口", "delivered"), ("交给保安了", "delivered"), ("本人已签字", "delivered"),
    ("东西已经拿走了 搞定", "delivered"), ("放到前台了", "delivered"),
    ("遇到事故绕道中", "delay"), ("电瓶车爆胎了 稍等", "delay"), ("前面在修路 要多二十分钟", "delay"),
    ("货还没装完 晚点出发", "delay"), ("雨太大了 得慢点开", "delay"),
    ("屋里一直没人回应", "not_home"), ("门锁着 敲了半天", "not_home"), ("到了楼下 他手机打不通", "not_home"),
    ("小区进不去 客户也不接", "not_home"), ("家里好像没人", "not_home"),
]