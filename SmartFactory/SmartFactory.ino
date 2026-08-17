/*
基于颜色识别的交互式RGB LED反馈系统设计
0.91寸 I2C OLED 128x32 版本

*/
#include<U8g2lib.h>
#include <math.h>
U8G2_SSD1306_128X32_UNIVISION_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);

// // 定义引脚
const int S0 = 2;
const int S1 = 3;
const int S2 = 4;
const int S3 = 5;
const int sensorOut = 6;//传感器把颜色结果传给 Arduino 的脚
int ledR = 9;
int ledG = 10;
int ledB = 11;

// 统一变量名：使用 R, G, B 代表红、绿及蓝色。
int R, G, B;//定义变量（存储 RGB 值）
char r_str[10];
char g_str[10];
char b_str[10];


void setup() {
  pinMode(S0, OUTPUT);
  pinMode(S1, OUTPUT);
  pinMode(S2, OUTPUT);
  pinMode(S3, OUTPUT);
  pinMode(sensorOut, INPUT);

  pinMode(ledR, OUTPUT);
  pinMode(ledG, OUTPUT);
  pinMode(ledB, OUTPUT);  


  // 频率 20%,传感器标准设置
  digitalWrite(S0, HIGH);
  digitalWrite(S1, LOW);

  u8g2.begin();//启动屏幕
  Serial.begin(9600);//启动串口（方便电脑看数据）
}

void loop() {//循环运行
  // --------------------
  // 读取红色
  digitalWrite(S2, LOW);
  digitalWrite(S3, LOW);
  R = pulseIn(sensorOut, HIGH);
  delay(50);
  // 读取绿色
  // --------------------
  digitalWrite(S2, HIGH);
  digitalWrite(S3, HIGH);
  G = pulseIn(sensorOut, HIGH);
  delay(50);
  // 读取蓝色
  // --------------------
  digitalWrite(S2, LOW);
  digitalWrite(S3, HIGH);
 B = pulseIn(sensorOut, HIGH);
  delay(50);
  // 映射成 0~255 标准RGB,把传感器读到的值 → 变成我们熟悉的 0~255 RGB 颜色值
 R = map(R, 18, 142, 255, 0);
 G = map(G, 19, 153, 255, 0);
 B = map(B, 16, 132, 255, 0);

  // 限制范围 0~255,确保数值不会超出 0~255，保证稳定
 R = constrain(R, 0, 255);
 G = constrain(G, 0, 255);
 B = constrain(B, 0, 255);
  
  // 自动颜色识别（根据你实测值酌情调整）
  // ======================
//   if ( R > 150 && G < 180 && B <200&& R>G&&R>B) {
//     strcpy(colorName, "Red");
//   } 
//   else if (G > 110&& R < 180 && B < 200&& G>R&&G>B) {
//     strcpy(colorName, "Green");
//   }
//   else if ( B > 180 && R < 170 && G <220&&B>R&&B>G) {
//     strcpy(colorName, "Blue");
//   }
//   else if (R > 175 && G > 180 && B > 170) {
//     strcpy(colorName, "White");
//   }
//   else if (R < 10 && G < 10 && B < 10) {
//     strcpy(colorName, "Black");
//   }
//   else {
//     strcpy(colorName, "None");
//   }

const char* colorName = getColorName(R, G, B).c_str();
 // RGB LED 灯光反馈
  // ==========================
  analogWrite(ledR,R); analogWrite(ledG,G); analogWrite(ledB,B);


  // 转字符串
  dtostrf(R, 3, 0, r_str);
  dtostrf(G, 3, 0, g_str);
  dtostrf(B, 3, 0, b_str);

  // ==========================
  // OLED 显示（你要的字体大小）
  // ==========================
  u8g2.firstPage();
  do {
    u8g2.setFont(u8g2_font_ncenB10_tr);

    u8g2.drawStr(0,12,"R:");
    u8g2.drawStr(20,12,r_str);

    u8g2.drawStr(60,12,"G:");
    u8g2.drawStr(80,12,g_str);

    u8g2.drawStr(0,30,"B:");
    u8g2.drawStr(20,30,b_str);

    u8g2.drawStr(60,30,"C:");
    u8g2.drawStr(80,30,colorName);

  } while (u8g2.nextPage());

  // 串口输出
  Serial.print("R:"); Serial.print(R);
  Serial.print(" G:"); Serial.print(G);
  Serial.print(" B:"); Serial.print(B);
  Serial.print(" Color:"); Serial.println(colorName);
}

// 核心函数：输入RGB，返回颜色名称字符串
String getColorName(uint16_t r, uint16_t g, uint16_t b) {
  // 1. 将 RGB 归一化到 0 ~ 1 之间
  float rf = r / 255.0;
  float gf = g / 255.0;
  float bf = b / 255.0;

  // 2. 找出 R, G, B 中的最大值和最小值
  float maxVal = max(rf, max(gf, bf));
  float minVal = min(rf, min(gf, bf));
  float delta = maxVal - minVal;

  // 3. 初始化 H(色相), S(饱和度), V(明度)
  float h = 0, s = 0, v = maxVal;

  // 计算饱和度 S
  if (maxVal != 0) {
    s = delta / maxVal;
  }

  // 计算色相 H (0-360度)
  if (delta != 0) {
    if (maxVal == rf) {
      h = 60 * fmod(((gf - bf) / delta), 6.0);
    } else if (maxVal == gf) {
      h = 60 * (((bf - rf) / delta) + 2.0);
    } else if (maxVal == bf) {
      h = 60 * (((rf - gf) / delta) + 4.0);
    }
    if (h < 0) h += 360.0; // 确保色相是正数
  }

  // --- 4. 核心判断逻辑 ---
  
  // 如果太暗，直接返回黑色
  if (v < 0.15) return "Black";
  
  // 如果饱和度太低，说明没有颜色倾向，是灰白黑
  if (s < 0.15) return "White"; // 或者你可以细分 Gray

  // 根据色相 H 的角度范围，精准划分颜色
  // 注意：红色的色相在 0度 和 360度 附近，所以分成了两段
  if (h < 15 || h >= 345)   return "Red";
  if (h >= 15 && h < 45)    return "Orange";
  if (h >= 45 && h < 75)    return "Yellow";
  if (h >= 75 && h < 150)   return "Green";
  if (h >= 150 && h < 195)  return "Cyan";
  if (h >= 195 && h < 260)  return "Blue";
  if (h >= 260 && h < 290)  return "Purple";
  if (h >= 290 && h < 345)  return "Pink";

  return "Unknown"; // 兜底
}