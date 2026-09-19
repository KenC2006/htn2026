// Frozen. Minimal JSON for the test harness: values in, values out. No crates.
#![allow(dead_code)]

#[derive(Debug, Clone)]
pub enum J { Null, Bool(bool), Num(String), Str(String), Arr(Vec<J>), Obj(Vec<(String, J)>) }

pub fn parse(text: &str) -> J {
    let chars: Vec<char> = text.chars().collect();
    let mut at = 0usize;
    let v = value(&chars, &mut at);
    v
}

fn ws(c: &[char], at: &mut usize) { while *at < c.len() && c[*at].is_whitespace() { *at += 1; } }

fn value(c: &[char], at: &mut usize) -> J {
    ws(c, at);
    match c[*at] {
        'n' => { *at += 4; J::Null }
        't' => { *at += 4; J::Bool(true) }
        'f' => { *at += 5; J::Bool(false) }
        '"' => J::Str(string(c, at)),
        '[' => {
            *at += 1; let mut items = Vec::new();
            loop { ws(c, at); if c[*at] == ']' { *at += 1; break; } if c[*at] == ',' { *at += 1; continue; } items.push(value(c, at)); }
            J::Arr(items)
        }
        '{' => {
            *at += 1; let mut items = Vec::new();
            loop {
                ws(c, at); if c[*at] == '}' { *at += 1; break; } if c[*at] == ',' { *at += 1; continue; }
                let k = string(c, at); ws(c, at); *at += 1; // ':'
                items.push((k, value(c, at)));
            }
            J::Obj(items)
        }
        _ => { let start = *at; while *at < c.len() && !",]} \n\r\t".contains(c[*at]) { *at += 1; } J::Num(c[start..*at].iter().collect()) }
    }
}

fn hex4(c: &[char], at: &mut usize) -> u32 { let s: String = c[*at..*at + 4].iter().collect(); *at += 4; u32::from_str_radix(&s, 16).unwrap() }

fn string(c: &[char], at: &mut usize) -> String {
    *at += 1; let mut out = String::new();
    while c[*at] != '"' {
        if c[*at] == '\\' {
            *at += 1; let e = c[*at]; *at += 1;
            match e {
                'n' => out.push('\n'), 't' => out.push('\t'), 'r' => out.push('\r'), 'b' => out.push('\u{8}'), 'f' => out.push('\u{c}'),
                'u' => {
                    let mut cp = hex4(c, at);
                    if (0xD800..0xDC00).contains(&cp) && *at + 1 < c.len() && c[*at] == '\\' && c[*at + 1] == 'u' {
                        *at += 2; let lo = hex4(c, at); cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    }
                    out.push(char::from_u32(cp).unwrap_or('\u{FFFD}'));
                }
                other => out.push(other),
            }
        } else { out.push(c[*at]); *at += 1; }
    }
    *at += 1; out
}

impl J {
    pub fn get(&self, key: &str) -> &J {
        if let J::Obj(items) = self { for (k, v) in items { if k == key { return v; } } }
        &J::Null
    }
}

pub trait FromJ: Sized { fn from_j(j: &J) -> Self; }
impl FromJ for i64 { fn from_j(j: &J) -> i64 { match j { J::Num(s) => s.parse::<i64>().unwrap_or_else(|_| s.parse::<f64>().expect("number") as i64), J::Bool(b) => *b as i64, _ => panic!("expected an integer") } } }
impl FromJ for f64 { fn from_j(j: &J) -> f64 { match j { J::Num(s) => s.parse().expect("number"), J::Str(s) => match s.as_str() { "NaN" => f64::NAN, "Infinity" => f64::INFINITY, "-Infinity" => f64::NEG_INFINITY, _ => panic!("expected a number") }, _ => panic!("expected a number") } } }
impl FromJ for bool { fn from_j(j: &J) -> bool { matches!(j, J::Bool(true)) } }
impl FromJ for String { fn from_j(j: &J) -> String { match j { J::Str(s) => s.clone(), _ => panic!("expected a string") } } }
impl<T: FromJ> FromJ for Vec<T> { fn from_j(j: &J) -> Vec<T> { match j { J::Arr(a) => a.iter().map(T::from_j).collect(), _ => panic!("expected a list") } } }
impl<T: FromJ> FromJ for Option<T> { fn from_j(j: &J) -> Option<T> { match j { J::Null => None, other => Some(T::from_j(other)) } } }

pub trait ToJ { fn to_j(&self) -> String; }
impl ToJ for i64 { fn to_j(&self) -> String { self.to_string() } }
impl ToJ for f64 { fn to_j(&self) -> String { if self.is_nan() { "\"NaN\"".into() } else if self.is_infinite() { if *self > 0.0 { "\"Infinity\"".into() } else { "\"-Infinity\"".into() } } else { format!("{}", self) } } }
impl ToJ for bool { fn to_j(&self) -> String { self.to_string() } }
impl ToJ for String { fn to_j(&self) -> String { self.as_str().to_j() } }
impl ToJ for &str {
    fn to_j(&self) -> String {
        let mut out = String::from("\"");
        for ch in self.chars() {
            match ch {
                '"' => out.push_str("\\\""), '\\' => out.push_str("\\\\"), '\n' => out.push_str("\\n"), '\r' => out.push_str("\\r"), '\t' => out.push_str("\\t"),
                c if (c as u32) < 0x20 || (c as u32) > 0x7e => { let mut buf = [0u16; 2]; for u in c.encode_utf16(&mut buf) { out.push_str(&format!("\\u{:04x}", u)); } }
                c => out.push(c),
            }
        }
        out.push('"'); out
    }
}
impl<T: ToJ> ToJ for Vec<T> { fn to_j(&self) -> String { format!("[{}]", self.iter().map(|x| x.to_j()).collect::<Vec<_>>().join(", ")) } }
impl<T: ToJ> ToJ for Option<T> { fn to_j(&self) -> String { match self { Some(x) => x.to_j(), None => "null".into() } } }
impl<A: ToJ, B: ToJ> ToJ for (A, B) { fn to_j(&self) -> String { format!("[{}, {}]", self.0.to_j(), self.1.to_j()) } }
impl<A: ToJ, B: ToJ, C: ToJ> ToJ for (A, B, C) { fn to_j(&self) -> String { format!("[{}, {}, {}]", self.0.to_j(), self.1.to_j(), self.2.to_j()) } }
impl<A: ToJ, B: ToJ, C: ToJ, D: ToJ> ToJ for (A, B, C, D) { fn to_j(&self) -> String { format!("[{}, {}, {}, {}]", self.0.to_j(), self.1.to_j(), self.2.to_j(), self.3.to_j()) } }
impl<A: ToJ, B: ToJ, C: ToJ, D: ToJ, E: ToJ> ToJ for (A, B, C, D, E) { fn to_j(&self) -> String { format!("[{}, {}, {}, {}, {}]", self.0.to_j(), self.1.to_j(), self.2.to_j(), self.3.to_j(), self.4.to_j()) } }
impl<A: ToJ, B: ToJ, C: ToJ, D: ToJ, E: ToJ, F: ToJ> ToJ for (A, B, C, D, E, F) { fn to_j(&self) -> String { format!("[{}, {}, {}, {}, {}, {}]", self.0.to_j(), self.1.to_j(), self.2.to_j(), self.3.to_j(), self.4.to_j(), self.5.to_j()) } }
fn items(j: &J, n: usize) -> &Vec<J> { match j { J::Arr(a) if a.len() == n => a, _ => panic!("expected a list of {} items", n) } }
impl<A: FromJ, B: FromJ> FromJ for (A, B) { fn from_j(j: &J) -> Self { let a = items(j, 2); (A::from_j(&a[0]), B::from_j(&a[1])) } }
impl<A: FromJ, B: FromJ, C: FromJ> FromJ for (A, B, C) { fn from_j(j: &J) -> Self { let a = items(j, 3); (A::from_j(&a[0]), B::from_j(&a[1]), C::from_j(&a[2])) } }
impl<A: FromJ, B: FromJ, C: FromJ, D: FromJ> FromJ for (A, B, C, D) { fn from_j(j: &J) -> Self { let a = items(j, 4); (A::from_j(&a[0]), B::from_j(&a[1]), C::from_j(&a[2]), D::from_j(&a[3])) } }
impl<T: ToJ> ToJ for Result<T, String> { fn to_j(&self) -> String { match self { Ok(v) => format!("{{\"ok\": {}}}", v.to_j()), Err(e) => format!("{{\"error\": {}}}", e.as_str().to_j()) } } }
